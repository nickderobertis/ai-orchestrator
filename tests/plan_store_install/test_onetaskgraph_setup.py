"""Exercise the release-archive installer without depending on GitHub availability.

Every journey here drives the real `install_onetaskgraph` from
`scripts/session-setup.sh` inside a throwaway checkout, with only the archive fetch
doubled — `tests/onetaskgraph_release.py` owns that boundary and says why.

The destination is the *checkout's own* `.venv/bin`, so these run against a copy of
this repository's provisioning rather than against this repository: installing a
fixture binary into the tree the suite is running in would replace the CLI every other
test here resolves.

Each assertion below states what it was checking. That is not a style preference: the
whole of this module used to be a bash script asserting with bare `test` lines and one
`assert result.returncode == 0` in Python, so a failure of any of four different
properties reported `assert 1 == 0` with empty stderr and named none of them.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest
from onetaskgraph_release import (
    FIXTURE_TARGET,
    checkout,
    fetch_double,
    installed_binary,
    path_without,
    release_fixture,
    run_installer,
)

from orchestrator.root import REPO_ROOT

#: The release the checkout under test pins. Read from this repository so a bump keeps
#: these journeys honest about the version the installer is really asked for.
ADOPTED = (REPO_ROOT / "config" / "onetaskgraph.version").read_text(encoding="utf-8").strip()

#: What the second `install_onetaskgraph` of an idempotence journey has to leave
#: untouched, as `stat` reports it: inode, modification time, and size. A reinstall
#: that rewrote the file in place would keep the inode and move the other two.
IDENTITY = "%i:%Y:%s"


def _install_twice(repo: Path, archive: Path, home: Path) -> subprocess.CompletedProcess[str]:
    """Run the installer, record the file's identity, run it again, record it again."""
    return run_installer(
        repo,
        archive,
        home,
        script=(
            "install_onetaskgraph || exit $?\n"
            f'printf "first=%s\\n" "$(stat -c \'{IDENTITY}\' "$ONETASKGRAPH_BIN")"\n'
            "install_onetaskgraph || exit $?\n"
            f'printf "second=%s\\n" "$(stat -c \'{IDENTITY}\' "$ONETASKGRAPH_BIN")"\n'
        ),
    )


def _reported(result: subprocess.CompletedProcess[str], key: str) -> str:
    """One `key=value` line the installer journey printed, or a failure naming it."""
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator and name == key:
            return value
    raise AssertionError(
        f"the installer journey printed no {key!r} line, so nothing here can say what "
        f"the installed binary looked like. It reported:\n{result.stdout}{result.stderr}"
    )


def test_a_valid_archive_installs_once_into_this_checkouts_own_environment(
    tmp_path: Path,
) -> None:
    """A valid release installs into `<root>/.venv/bin`, and installing again is a no-op."""
    repo = checkout(tmp_path / "repo")
    archive = release_fixture(tmp_path, ADOPTED)
    home = tmp_path / "home"

    result = _install_twice(repo, archive, home)

    assert result.returncode == 0, (
        f"installing {ADOPTED} from a checksum-valid archive failed:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert installed_binary(repo).is_file(), (
        f"the installer reported success without leaving a binary at "
        f"{installed_binary(repo)}, which is where this checkout's own provisioning "
        "puts it"
    )
    assert _reported(result, "first") == _reported(result, "second"), (
        "the second install_onetaskgraph replaced an already-verified binary; it is "
        "run on every session start, so a reinstall each time is the churn this "
        "installer exists to avoid"
    )
    assert not (home / ".local" / "bin" / "onetaskgraph").exists(), (
        "the installer wrote to the host-shared $HOME/.local/bin, which is the path "
        "two checkouts at two pins overwrote each other on"
    )


def test_provisioning_creates_the_plan_root_a_source_refuses_to_read_without(
    tmp_path: Path,
) -> None:
    """A verified install leaves the gitignored `local-md` root every source read needs."""
    repo = checkout(tmp_path / "repo")
    result = run_installer(repo, release_fixture(tmp_path, ADOPTED), tmp_path / "home")

    assert result.returncode == 0, f"installation failed:\n{result.stdout}{result.stderr}"
    assert (repo / ".plans" / "tasks").is_dir(), (
        "provisioning left no .plans/tasks; onetaskgraph refuses a root it cannot "
        "canonicalize for the whole read, so one absent directory exits every source"
    )
    assert (repo / ".plans" / "projects").is_dir(), (
        "provisioning left no .plans/projects; onetaskgraph refuses a root it cannot "
        "canonicalize for the whole read, so one absent directory exits every source"
    )


def test_provisioning_does_not_recreate_the_retired_local_plan_root(tmp_path: Path) -> None:
    """The `plans-local` root the retreat used stays gone.

    Recreating it puts a stale copy of this repository's plans beside the board it now
    plans on, which is the failure the retreat's removal was for.
    """
    repo = checkout(tmp_path / "repo")
    result = run_installer(repo, release_fixture(tmp_path, ADOPTED), tmp_path / "home")

    assert result.returncode == 0, f"installation failed:\n{result.stdout}{result.stderr}"
    assert not (repo / ".plans-local").exists(), (
        "provisioning recreated .plans-local, the retired store root; a stale copy of "
        "this repository's plans then sits beside the board it plans on"
    )


def test_archive_install_refuses_a_checksum_mismatch(tmp_path: Path) -> None:
    """Downloaded bytes cannot be installed unless the sibling checksum authenticates them."""
    repo = checkout(tmp_path / "repo")
    archive = release_fixture(tmp_path, ADOPTED, valid_checksum=False)

    result = run_installer(repo, archive, tmp_path / "home")

    assert result.returncode != 0, (
        f"an archive whose sidecar does not authenticate it was installed anyway:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "checksum verification failed" in result.stderr, (
        f"the refusal did not name the checksum as the reason:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"unauthenticated bytes reached {installed_binary(repo)}"
    )


def test_archive_install_refuses_a_malformed_checksum_sidecar(tmp_path: Path) -> None:
    """A sidecar without one hexadecimal SHA-256 digest cannot authorize an install."""
    repo = checkout(tmp_path / "repo")
    archive = release_fixture(tmp_path, ADOPTED)
    archive.with_suffix(archive.suffix + ".sha256").write_text("not-a-checksum\n", encoding="utf-8")

    result = run_installer(repo, archive, tmp_path / "home")

    assert result.returncode != 0, (
        f"a sidecar carrying no digest authorized an install:\n{result.stdout}{result.stderr}"
    )
    assert "checksum verification failed" in result.stderr, (
        f"the refusal did not name the checksum as the reason:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"bytes no digest authenticated reached {installed_binary(repo)}"
    )


def test_invalid_pin_is_refused_before_session_provisioning(tmp_path: Path) -> None:
    """A malformed standalone-tool release declaration fails by file and value."""
    repo = checkout(tmp_path / "repo")
    (repo / "config/onetaskgraph.version").write_text("next\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(repo / "scripts/session-setup.sh")],
        env={**os.environ, "CI": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0, (
        f"session setup accepted a pin that is not a version:\n{result.stdout}{result.stderr}"
    )
    assert "invalid adopted onetaskgraph version" in result.stderr, (
        f"the refusal did not name the pin as what was invalid:\n{result.stderr}"
    )
    assert "onetaskgraph.version: 'next'" in result.stderr, (
        f"the refusal named neither the file nor the value it read:\n{result.stderr}"
    )


def test_unsupported_platform_is_refused_by_name(tmp_path: Path) -> None:
    """A host without a published target gets an attributable refusal."""
    command = r"""
source scripts/session-setup.sh
uname() { if [ "${1:-}" = -m ]; then echo mips; else echo Plan9; fi; }
onetaskgraph_target
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0, (
        f"a platform with no published archive resolved a target anyway: {result.stdout!r}"
    )
    assert "no release archive for Plan9 mips" in result.stderr, (
        f"the refusal did not name the platform it could not serve:\n{result.stderr}"
    )


@pytest.mark.parametrize(
    ("system", "machine", "target"),
    (
        ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
        ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
        ("Darwin", "x86_64", "x86_64-apple-darwin"),
        ("Darwin", "arm64", "aarch64-apple-darwin"),
    ),
)
def test_supported_platform_selects_its_release_archive(
    tmp_path: Path, system: str, machine: str, target: str
) -> None:
    """Every platform advertised by the installer resolves through its real case table."""
    command = r"""
source scripts/session-setup.sh
uname() { if [ "${1:-}" = -m ]; then echo "$MACHINE"; else echo "$SYSTEM"; fi; }
onetaskgraph_target
"""
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=REPO_ROOT,
        env={**os.environ, "HOME": str(tmp_path), "SYSTEM": system, "MACHINE": machine},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"{system} {machine} is advertised as supported and resolved no target:\n{result.stderr}"
    )
    assert result.stdout.strip() == target, (
        f"{system} {machine} resolved {result.stdout.strip()!r}, not the published "
        f"target {target!r}"
    )


def test_archive_download_failure_is_required(tmp_path: Path) -> None:
    """A missing release asset fails installation with its adopted version."""
    repo = checkout(tmp_path / "repo")
    result = run_installer(
        repo,
        tmp_path / "absent.tar.gz",
        tmp_path / "home",
        prelude="curl() { return 1; }",
    )

    assert result.returncode != 0, (
        f"an unreachable release asset installed something anyway:\n{result.stdout}{result.stderr}"
    )
    assert "release archive download failed" in result.stderr, (
        f"the refusal did not name the download as what failed:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a failed download still left a binary at {installed_binary(repo)}"
    )


def test_temporary_directory_creation_failure_is_required(tmp_path: Path) -> None:
    """Installation stops before downloading when no private staging directory can be made."""
    repo = checkout(tmp_path / "repo")
    result = run_installer(
        repo,
        release_fixture(tmp_path, ADOPTED),
        tmp_path / "home",
        prelude="mktemp() { return 1; }",
    )

    assert result.returncode != 0, (
        f"installation continued without a staging directory:\n{result.stdout}{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a binary reached {installed_binary(repo)} with nowhere to stage it first"
    )


def test_checksum_sidecar_download_failure_is_required(tmp_path: Path) -> None:
    """A downloaded archive is discarded when its checksum sidecar is unavailable."""
    repo = checkout(tmp_path / "repo")
    result = run_installer(
        repo,
        release_fixture(tmp_path, ADOPTED),
        tmp_path / "home",
        prelude=r"""
curl() {
  local destination="${@: -1}"
  if [[ "$destination" == *.sha256 ]]; then
    return 1
  fi
  cp "$ARCHIVE" "$destination"
}
""",
    )

    assert result.returncode != 0, (
        f"an archive with no sidecar to authenticate it was installed:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "release archive download failed" in result.stderr, (
        f"the refusal did not name the download as what failed:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"unauthenticated bytes reached {installed_binary(repo)}"
    )


def test_shasum_fallback_verifies_and_installs_archive(tmp_path: Path) -> None:
    """macOS-style hosts without sha256sum authenticate the archive with shasum."""
    repo = checkout(tmp_path / "repo")
    called = tmp_path / "shasum-called"
    result = run_installer(
        repo,
        release_fixture(tmp_path, ADOPTED),
        tmp_path / "home",
        prelude=r"""
command() {
  if [[ "$1" == -v && "$2" == sha256sum ]]; then return 1; fi
  builtin command "$@"
}
shasum() {
  test "$1" = -a && test "$2" = 256 || return 2
  touch "$SHASUM_CALLED"
  /usr/bin/sha256sum "$3"
}
""",
        environment={"SHASUM_CALLED": str(called)},
    )

    assert result.returncode == 0, (
        f"a host without sha256sum could not install:\n{result.stdout}{result.stderr}"
    )
    assert called.is_file(), (
        "sha256sum was reported absent and shasum was never called, so this journey "
        "proved the ordinary path rather than the fallback"
    )
    assert installed_binary(repo).is_file(), (
        f"the shasum fallback verified the archive and left nothing at {installed_binary(repo)}"
    )


def test_destination_directory_failure_is_required(tmp_path: Path) -> None:
    """Installation fails clearly when the destination directory cannot be created."""
    repo = checkout(tmp_path / "repo")
    (repo / ".venv").write_text("not a directory", encoding="utf-8")

    result = run_installer(repo, release_fixture(tmp_path, ADOPTED), tmp_path / "home")

    assert result.returncode != 0, (
        f"installation reported success with no destination to install into:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "release archive installation failed" in result.stderr, (
        f"the refusal did not name the installation as what failed:\n{result.stderr}"
    )


def test_installed_binary_version_verification_failure_is_required(tmp_path: Path) -> None:
    """A checksum-valid binary that reports the wrong version is not accepted."""
    repo = checkout(tmp_path / "repo")
    archive = release_fixture(tmp_path, ADOPTED, reported_version="0.0.0")

    result = run_installer(repo, archive, tmp_path / "home")

    assert result.returncode != 0, (
        f"a binary reporting 0.0.0 was accepted for the pin {ADOPTED}:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert installed_binary(repo).exists(), (
        "this journey is about the *verification* refusing a binary that landed, and "
        f"nothing landed at {installed_binary(repo)}"
    )


def test_archive_missing_expected_binary_is_not_installed(tmp_path: Path) -> None:
    """A valid tar archive without the named CLI payload fails installation."""
    repo = checkout(tmp_path / "repo")
    payload = tmp_path / "readme"
    payload.write_text("no binary here", encoding="utf-8")
    archive = tmp_path / f"onetaskgraph-v{ADOPTED}-{FIXTURE_TARGET}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="README")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )

    result = run_installer(repo, archive, tmp_path / "home")

    assert result.returncode != 0, (
        f"an archive carrying no CLI installed successfully:\n{result.stdout}{result.stderr}"
    )
    assert "release archive installation failed" in result.stderr, (
        f"the refusal did not name the installation as what failed:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"something reached {installed_binary(repo)} from an archive with no CLI in it"
    )


def test_final_binary_install_failure_is_required(tmp_path: Path) -> None:
    """A destination copy failure is reported even after successful extraction."""
    repo = checkout(tmp_path / "repo")
    result = run_installer(
        repo,
        release_fixture(tmp_path, ADOPTED),
        tmp_path / "home",
        prelude="install() { return 1; }",
    )

    assert result.returncode != 0, (
        f"a failed copy to the destination was reported as success:\n{result.stdout}{result.stderr}"
    )
    assert "release archive installation failed" in result.stderr, (
        f"the refusal did not name the installation as what failed:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a binary reached {installed_binary(repo)} through a copy that failed"
    )


def test_plan_root_creation_failure_is_required(tmp_path: Path) -> None:
    """A verified installation still fails if its plan-store root cannot be created.

    A source refuses a root it cannot canonicalize for the whole read rather than for
    itself alone — so an installation that reported success with it missing would leave
    `just plans` exiting 4 on every source.
    """
    repo = checkout(tmp_path / "repo")
    (repo / ".plans").write_text("not a directory", encoding="utf-8")

    result = run_installer(repo, release_fixture(tmp_path, ADOPTED), tmp_path / "home")

    assert result.returncode != 0, (
        f"installation reported success with no plan root to read from:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert installed_binary(repo).exists(), (
        "this journey is about the plan root failing *after* a good install, and the "
        f"binary never reached {installed_binary(repo)}"
    )
    assert "Not a directory" in result.stderr, (
        f"the failure did not name the unusable plan root:\n{result.stderr}"
    )


def test_invalid_archive_is_not_installed(tmp_path: Path) -> None:
    """Checksum-valid bytes must still contain the expected executable payload."""
    repo = checkout(tmp_path / "repo")
    archive = tmp_path / f"onetaskgraph-v{ADOPTED}-{FIXTURE_TARGET}.tar.gz"
    archive.write_bytes(b"not a tar archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )

    result = run_installer(repo, archive, tmp_path / "home")

    assert result.returncode != 0, (
        f"bytes that are not a tar archive installed successfully:\n{result.stdout}{result.stderr}"
    )
    assert "release archive installation failed" in result.stderr, (
        f"the refusal did not name the installation as what failed:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"something reached {installed_binary(repo)} from bytes that are not an archive"
    )


#: How long a refusal is given before this reports the wrapper as hung. Bounded at all
#: because one sabotage below answers by blocking rather than by failing, which without
#: this wedges the suite instead of failing it.
REFUSAL_SECONDS = 120


def _wrapper(repo: Path, home: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    """Run `scripts/onetaskgraph-install.sh` in ``repo`` and report what it said.

    Nothing is doubled: every journey below is about a refusal the wrapper reaches
    before it would fetch anything, so there is no boundary to stand in for.
    """
    try:
        return subprocess.run(
            ["bash", "scripts/onetaskgraph-install.sh", *environment.pop("arguments", "").split()],
            cwd=repo,
            env={**os.environ, "HOME": str(home), **environment},
            text=True,
            capture_output=True,
            check=False,
            timeout=REFUSAL_SECONDS,
        )
    except subprocess.TimeoutExpired as hung:
        raise AssertionError(
            f"the wrapper neither provisioned nor refused within {REFUSAL_SECONDS}s, so it is "
            "blocked on something rather than answering; a path it creates or opens without "
            "establishing what is there first is the way that happens — a fifo answers "
            "neither an `O_CREAT` open nor a read-only one until somebody is on the other end"
        ) from hung


def test_the_self_heal_refuses_an_argument_rather_than_ignoring_it(tmp_path: Path) -> None:
    """There is no flag to reinstall with, and a caller who passed one is told so.

    Silently ignoring it is the worse answer: the caller believes a refresh happened
    and the binary they were reinstalling over is still the one they had.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)

    result = _wrapper(repo, tmp_path / "home", arguments="--force")

    assert result.returncode == 2, (
        f"an argument this wrapper takes none of was accepted:\n{result.stdout}{result.stderr}"
    )
    assert "expected no arguments, got '--force'" in result.stderr, (
        f"the refusal did not name the argument it was given:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a refused invocation installed something at {installed_binary(repo)}"
    )


def test_the_self_heal_keeps_an_environment_a_caller_provided(tmp_path: Path) -> None:
    """`UV_NO_SYNC` means "the environment is provided", and this binary lives in it.

    Read as uv reads it, which is why the journey below refuses a value uv does not.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)

    result = _wrapper(repo, tmp_path / "home", UV_NO_SYNC="1")

    assert result.returncode == 0, (
        f"a provided environment was not left alone:\n{result.stdout}{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"UV_NO_SYNC=1 says the environment is provided, and {installed_binary(repo)} "
        "was installed into it anyway"
    )


def test_the_self_heal_refuses_a_uv_no_sync_uv_would_not_read(tmp_path: Path) -> None:
    """A value uv does not read is a caller who thinks they turned this off.

    Guessing either way is worse than refusing: read as false it provisions over the
    environment they meant to keep, and read as true it silently skips the heal.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)

    result = _wrapper(repo, tmp_path / "home", UV_NO_SYNC="maybe")

    assert result.returncode == 2, (
        f"a UV_NO_SYNC uv does not read was acted on:\n{result.stdout}{result.stderr}"
    )
    assert "UV_NO_SYNC='maybe' is not a value uv reads" in result.stderr, (
        f"the refusal did not name the value it could not read:\n{result.stderr}"
    )


def test_a_workspace_declaring_no_adopted_release_has_nothing_to_provision(
    tmp_path: Path,
) -> None:
    """`tests/fixtures/nx-cache` is one, and every Nx invocation runs this wrapper.

    A tree with no plan store to read must not gain a network crossing in front of
    every Nx target, so the absence of the pin is a no-op rather than a refusal.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    (repo / "config" / "onetaskgraph.version").unlink()

    result = _wrapper(repo, tmp_path / "home")

    assert result.returncode == 0, (
        "a workspace declaring no adopted release was refused rather than skipped:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert result.stderr == "", (
        f"the no-op said something an operator has to read:\n{result.stderr}"
    )
    # The install lock is taken below this check rather than above it, so that a tree
    # with nothing to provision is left exactly as it was found. `checkout` copies only
    # `scripts/` and `config/`, so this directory exists only if the wrapper made one.
    assert not (repo / ".logs").exists(), (
        "a workspace with no adopted release gained the wrapper's own lock directory at "
        f"{repo / '.logs'}; serializing an install it never performs leaves state behind "
        "in every tree that only runs Nx"
    )


def test_the_self_heal_names_the_helper_it_could_not_load(tmp_path: Path) -> None:
    """It defines no install of its own, so a missing helper is what it cannot do.

    Sourcing something that is not a readable regular file fails as shell noise
    naming nothing, which is the failure this validation replaces.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    (repo / "scripts" / "session-setup.sh").unlink()

    result = _wrapper(repo, tmp_path / "home")

    assert result.returncode == 1, (
        f"a checkout missing the helper provisioned anyway:\n{result.stdout}{result.stderr}"
    )
    assert "required helper is not a readable regular file" in result.stderr, (
        f"the refusal did not name the helper as what was missing:\n{result.stderr}"
    )
    assert "just bootstrap" in result.stderr, (
        f"the refusal named no way to recover from it:\n{result.stderr}"
    )


def test_the_self_heal_reports_a_helper_that_refuses_to_load(tmp_path: Path) -> None:
    """The helper declines a pin it cannot parse, and this is where that surfaces.

    The reason is almost always the adopted version file, which the helper names on
    its own way out; what this adds is the wrapper saying which file to correct.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    (repo / "config" / "onetaskgraph.version").write_text("next\n", encoding="utf-8")

    result = _wrapper(repo, tmp_path / "home")

    assert result.returncode == 1, (
        f"a checkout pinned to a non-version provisioned anyway:\n{result.stdout}{result.stderr}"
    )
    assert "invalid adopted onetaskgraph version" in result.stderr, (
        f"the helper's own reason did not reach the caller:\n{result.stderr}"
    )
    assert "refused to load" in result.stderr, (
        f"the wrapper added nothing to the helper's refusal:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a refused load still installed something at {installed_binary(repo)}"
    )


def test_the_self_heal_names_the_install_it_could_not_perform(tmp_path: Path) -> None:
    """An install that fails under this entry point says so in the wrapper's own words.

    The helper names the step — a download, a checksum, an unpack — and the wrapper
    adds the two things the helper cannot know from inside: which checkout's binary
    was being provisioned, and the command that performs the same install by hand.
    `curl` is doubled with one that always fails, which is the boundary this reaches.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    unreachable = fetch_double(tmp_path, None)

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(unreachable), os.environ["PATH"])),
    )

    assert result.returncode == 1, (
        f"an unreachable release installed something anyway:\n{result.stdout}{result.stderr}"
    )
    assert "release archive download failed" in result.stderr, (
        f"the helper's own reason did not reach the caller:\n{result.stderr}"
    )
    assert str(installed_binary(repo)) in result.stderr, (
        f"the refusal did not name the binary it could not provision:\n{result.stderr}"
    )
    assert "just session-setup" in result.stderr, (
        f"the refusal named no way to perform the same install:\n{result.stderr}"
    )


#: How long one fetch takes. Orders of magnitude longer than spawning the callers below,
#: so they certainly overlap; it buys that overlap rather than the assertions, which hold
#: for a straggler arriving after the winner released.
FETCH_SECONDS = 0.4

#: How many callers race for one unprovisioned destination. More than two, so a caller
#: that neither wins the lock nor is the first to wait on it is covered too.
RACERS = 4

#: What one provisioning install crosses the fetch boundary for: the release archive and
#: the checksum sidecar that authenticates it.
FETCHES_PER_INSTALL = 2


def test_concurrent_self_heals_provision_once_and_every_caller_succeeds(tmp_path: Path) -> None:
    """Callers racing for one unprovisioned destination all succeed, and one installs.

    The shape a publication meets: it verifies in a fresh clone, unprovisioned because
    session setup runs on a hook a clone never fires, across several workers at once.

    Serialization is read off the fetch boundary rather than off the lock: the winner
    crosses it once and every caller behind it takes the already-provisioned fast path,
    where an unserialized wrapper crosses once per caller.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    home = tmp_path / "home"
    home.mkdir()
    fetched = tmp_path / "fetched"
    serving = fetch_double(
        tmp_path,
        release_fixture(tmp_path, ADOPTED),
        delay=FETCH_SECONDS,
        trace=fetched,
    )
    environment = {
        **os.environ,
        "HOME": str(home),
        "PATH": os.pathsep.join((str(serving), os.environ["PATH"])),
    }

    racing = [
        subprocess.Popen(
            ["bash", "scripts/onetaskgraph-install.sh"],
            cwd=repo,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(RACERS)
    ]
    outcomes = [caller.communicate(timeout=180) for caller in racing]

    assert [caller.returncode for caller in racing] == [0] * RACERS, (
        f"a caller racing {RACERS - 1} others for one unprovisioned destination was "
        f"refused rather than waiting for it:\n{outcomes}"
    )
    reported = subprocess.run(
        [str(installed_binary(repo)), "--version"], text=True, capture_output=True, check=False
    )
    assert reported.stdout.strip() == f"onetaskgraph {ADOPTED}", (
        f"{RACERS} concurrent callers left {reported.stdout.strip()!r} at "
        f"{installed_binary(repo)}, and this checkout adopts {ADOPTED}"
    )
    crossings = fetched.read_text(encoding="utf-8").splitlines()
    assert len(crossings) == FETCHES_PER_INSTALL, (
        f"{len(crossings)} fetches crossed the boundary where one provisioning install "
        f"crosses it {FETCHES_PER_INSTALL} times, for the archive and its checksum "
        f"sidecar; a caller that did not wait for the winner installed over it:\n{crossings}"
    )


def _stat_double(front: Path, reports: str | None, *, descriptor: int) -> None:
    """A `stat` on ``front`` answering ``reports`` — or failing — for one descriptor only.

    The wrapper reads back every descriptor it opens, and both answers that matter — an
    unreadable one and a wrong one — are otherwise reachable only by winning a race
    against it. Doubling the tool drives the real branch. Narrowed to one descriptor
    because the wrapper reads the directory's before the lock file's, so a double that
    answered for every call could only ever reach the first of them.
    """
    real = shutil.which("stat")
    assert real is not None, "this host has no stat to defer to"
    answer = (
        'echo "stat: cannot stat this file" >&2\n      exit 1\n'
        if reports is None
        else f"printf '%s\\n' {shlex.quote(reports)}\n      exit 0\n"
    )
    double = front / "stat"
    double.write_text(
        "#!/usr/bin/env bash\n"
        'case "${@: -1}" in\n'
        f"  */fd/{descriptor})\n"
        f"      {answer}"
        "      ;;\n"
        "esac\n"
        f'exec {shlex.quote(real)} "$@"\n',
        encoding="utf-8",
    )
    double.chmod(0o755)


def _swap_the_lock_directory_while_it_is_secured(front: Path, repo: Path, tmp_path: Path) -> None:
    """Put a second `.logs` at that name while `chmod` is securing the first.

    The one window the descriptor binding cannot cover: both locks are created through
    `$lock_dir` again, because no portable shell can create *inside* a descriptor. `chmod`
    is what runs in that window and only when the directory arrives unsecured, so a
    double that swaps the name while it works is what losing that race looks like — and
    the descriptor goes on holding the original, which is what makes the name wrong.
    """
    logs = repo / ".logs"
    logs.mkdir(mode=0o755)
    elsewhere = tmp_path / "elsewhere-swapped-lock-directory"
    elsewhere.mkdir(mode=0o700)
    real = shutil.which("chmod")
    assert real is not None, "this host has no chmod to defer to"
    swapping = front / "chmod"
    swapping.write_text(
        "#!/usr/bin/env bash\n"
        f"mv {shlex.quote(str(logs))} {shlex.quote(str(logs) + '.moved')}\n"
        f"mv {shlex.quote(str(elsewhere))} {shlex.quote(str(logs))}\n"
        f'exec {shlex.quote(real)} "$@"\n',
        encoding="utf-8",
    )
    swapping.chmod(0o755)


def _refused_serialization(repo: Path, tmp_path: Path, mode: str) -> Path:
    """Break one piece of the wrapper's own serialization state; answer a `PATH` front.

    The front carries a `curl` that always fails, so nothing below could reach a release
    even if the refusal under test stopped refusing — which is what makes "this install
    never happened" an assertion rather than a hope.
    """
    front = fetch_double(tmp_path, None, name=f"front-{mode}")
    logs = repo / ".logs"
    match mode:
        case "file-at-lock-directory":
            # A regular file where the directory belongs. Refused before `mkdir`, which
            # reports it exactly as it reports an unwritable parent while the repair is
            # the opposite one — the case below is that other repair.
            logs.write_text("not a directory\n", encoding="utf-8")
        case "uncreatable-lock-directory":
            # The failure `mkdir` is left to report once the case above is taken from
            # it: a parent this user cannot write. The caller restores the mode.
            repo.chmod(0o555)
        case "unreadable-link-count":
            # No `.logs` here, so `mkdir -m 700` creates it and the first descriptor
            # `stat` is asked about is the lock file's. Refusing when neither form
            # answers is the safe direction: a count nothing could read establishes
            # nothing about whose inode this wrapper is holding.
            logs.mkdir()
            _stat_double(front, None, descriptor=9)
        case "replaced-lock" | "unlinked-lock":
            # The race itself, driven rather than waited for: `stat` is called between
            # the open and the checks below it, so a double that disturbs the file while
            # it answers is exactly what losing that race looks like.
            #
            # `replaced-lock` keeps the held inode at one name — it links it aside first,
            # so the swap leaves that link behind — and the name then points at a
            # different inode, which is what only the binding catches. `unlinked-lock`
            # simply removes it, and the count reaching nought is what says so.
            logs.mkdir()
            real = shutil.which("stat")
            assert real is not None, "this host has no stat to defer to"
            lock = logs / "onetaskgraph-install.lock"
            lock.touch()
            disturb = (
                f"rm -f {shlex.quote(str(lock))}\n"
                if mode == "unlinked-lock"
                else (
                    f"ln {shlex.quote(str(lock))} {shlex.quote(str(logs / 'kept'))}\n"
                    f"      : >{shlex.quote(str(logs / 'other'))}\n"
                    f"      mv -f {shlex.quote(str(logs / 'other'))} {shlex.quote(str(lock))}\n"
                )
            )
            swapping = front / "stat"
            swapping.write_text(
                "#!/usr/bin/env bash\n"
                'case "${@: -1}" in\n'
                "  */fd/9)\n"
                f"      {disturb}"
                "      ;;\n"
                "esac\n"
                f'exec {shlex.quote(real)} "$@"\n',
                encoding="utf-8",
            )
            swapping.chmod(0o755)
        case "mistyped-lock":
            # The descriptor read back as something other than a regular file. Only a
            # race reaches it for real, so `stat` is what answers differently — which
            # drives the branch through the real script rather than around it.
            logs.mkdir()
            _stat_double(front, "directory 1", descriptor=9)
        case "unreadable-lock-directory":
            # The same refusal one descriptor earlier: `.logs` is already there, so it
            # is opened and read back before the lock file is reached at all.
            logs.mkdir()
            _stat_double(front, None, descriptor=8)
        case "mistyped-lock-directory":
            # And that descriptor reading back as something that is not a directory.
            logs.mkdir()
            _stat_double(front, "regular file 700", descriptor=8)
        case "unoctal-lock-directory" | "uncounted-lock":
            # The right type and a second field that is not the number read off it. What
            # this stands for is a `stat` whose output the wrapper cannot parse at all —
            # a diagnostic on stdout, a format this platform spells differently — since
            # what makes either dangerous is the same thing: a mode or a link count
            # taken from a string that was never one, and then compared against 700 or
            # against 1 and found unequal, which is a refusal for the wrong reason at
            # best and a mode nobody chose at worst.
            logs.mkdir()
            if mode == "unoctal-lock-directory":
                _stat_double(front, "directory rwx", descriptor=8)
            else:
                _stat_double(front, "regular file many", descriptor=9)
        case "unreadable-lock" | "write-only-lock":
            logs.mkdir()
            lock = logs / "onetaskgraph-install.lock"
            lock.touch()
            lock.chmod(0o000 if mode == "unreadable-lock" else 0o200)
        case "unchmodable-lock-directory":
            # A directory already there and not already secured, which is the only case
            # that reaches `chmod` at all: one this wrapper creates is 700 from `mkdir`.
            # Doubled at `chmod` because owning a directory is not something a test can
            # stop doing.
            logs.mkdir(mode=0o755)
            refusing = front / "chmod"
            refusing.write_text(
                '#!/usr/bin/env bash\necho "chmod: operation not permitted" >&2\nexit 1\n',
                encoding="utf-8",
            )
            refusing.chmod(0o755)
        case "non-regular-lock":
            # A fifo where the lock file belongs. The one sabotage here that an
            # unestablished path answers by blocking rather than by failing: `>>` on a
            # fifo waits for a reader, so the wrapper hangs and says nothing at all.
            logs.mkdir()
            os.mkfifo(logs / "onetaskgraph-install.lock")
        case "replaced-lock-directory" | "relinked-lock-directory":
            # The directory's own race, driven the way the lock file's is: `stat` is
            # called between its open and the binding below, so a double that swaps the
            # name while it answers is what losing that race looks like. One swaps in a
            # second directory, the other a link out of the checkout; the descriptor goes
            # on holding the original either way, which is what makes the name wrong.
            logs.mkdir()
            real = shutil.which("stat")
            assert real is not None, "this host has no stat to defer to"
            elsewhere = tmp_path / f"elsewhere-{mode}"
            elsewhere.mkdir()
            swap = (
                f"ln -s {shlex.quote(str(elsewhere))} {shlex.quote(str(logs))}\n"
                if mode == "relinked-lock-directory"
                else f"mkdir {shlex.quote(str(logs))}\n"
            )
            swapping = front / "stat"
            swapping.write_text(
                "#!/usr/bin/env bash\n"
                'case "${@: -1}" in\n'
                "  */fd/8)\n"
                f"      mv {shlex.quote(str(logs))} {shlex.quote(str(logs)) + '.moved'}\n"
                f"      {swap}"
                "      ;;\n"
                "esac\n"
                f'exec {shlex.quote(real)} "$@"\n',
                encoding="utf-8",
            )
            swapping.chmod(0o755)
        case "unopenable-lock-directory":
            # A directory this user may not read: `mkdir` refuses it as already there
            # and the descriptor cannot be opened, which is the one refusal between
            # them. The caller restores the mode.
            logs.mkdir(mode=0o000)
        case "swapped-lock-directory":
            _swap_the_lock_directory_while_it_is_secured(front, repo, tmp_path)
        case "unacquirable-lock":
            # The one refusal no permission can produce: `flock` itself failing.
            flock = front / "flock"
            flock.write_text(
                '#!/usr/bin/env bash\necho "flock: cannot lock this file" >&2\nexit 1\n',
                encoding="utf-8",
            )
            flock.chmod(0o755)
        case _:  # pragma: no cover - guards the parametrization below
            raise AssertionError(f"unknown serialization sabotage {mode!r}")
    return front


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("file-at-lock-directory", "move whatever is at that path aside"),
        ("uncreatable-lock-directory", "repair its parent's permissions"),
        ("unchmodable-lock-directory", "run 'chmod 700"),
        ("unreadable-link-count", "cannot read back what the install lock"),
        ("mistyped-lock", "rather than a regular file"),
        ("replaced-lock", "no longer names the inode this wrapper opened"),
        ("unlinked-lock", "was unlinked while this wrapper was opening it"),
        ("unreadable-lock-directory", "establish it is a directory"),
        ("mistyped-lock-directory", "rather than a directory"),
        ("unoctal-lock-directory", "whose mode field is not octal"),
        ("uncounted-lock", "whose link-count field is not a number"),
        ("unreadable-lock", "cannot open the install lock at"),
        ("write-only-lock", "cannot open the install lock at"),
        ("non-regular-lock", "is not a regular file"),
        ("replaced-lock-directory", "no longer names the directory this wrapper opened"),
        ("relinked-lock-directory", "no longer names the directory this wrapper opened"),
        ("unopenable-lock-directory", "cannot open"),
        ("unacquirable-lock", "flock could not lock"),
        ("swapped-lock-directory", "was replaced while this wrapper was taking"),
    ],
)
def test_the_self_heal_names_the_serialization_it_could_not_take(
    tmp_path: Path, mode: str, message: str
) -> None:
    """A caller that cannot serialize is refused, in its sibling installer's own shape.

    Provisioning unserialized is the one thing it must not do instead, so every way the
    lock can refuse names what could not be done and what repairs it — including the
    `exec` redirection a script otherwise dies on without a word.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = _refused_serialization(repo, tmp_path, mode)

    try:
        result = _wrapper(
            repo,
            tmp_path / "home",
            PATH=os.pathsep.join((str(front), os.environ["PATH"])),
        )
    finally:
        # Two modes take a permission away to reach their refusal — the checkout's own
        # write bit, and the lock directory's read bit. Left that way it is the teardown
        # that fails rather than the assertion.
        repo.chmod(0o755)
        if (repo / ".logs").is_dir():
            (repo / ".logs").chmod(0o700)

    assert result.returncode == 1, (
        f"a caller that could not take the install lock provisioned anyway:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert message in result.stderr, (
        f"the refusal did not name what it could not do:\n{result.stderr}"
    )
    assert "retry" in result.stderr, (
        f"the refusal named nothing an operator does about it:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a caller that never took the install lock still provisioned {installed_binary(repo)}"
    )


#: What a directory outside the checkout is left at, and what it must still be at
#: afterwards. Any mode `chmod 700` would move works; 0o755 is the ordinary one a
#: shared directory carries, so a wrapper that followed a link to it is visible as the
#: permission loss an operator would actually suffer.
OUTSIDE_MODE = 0o755


def test_a_linked_lock_directory_is_refused_rather_than_secured_outside_the_checkout(
    tmp_path: Path,
) -> None:
    """`.logs` is secured with `chmod`, and `chmod` changes what a link points at.

    So state this wrapper did not create decides which directory it takes permissions
    away from. Measured before it was refused: a `.logs` linked at a 0o755 directory
    elsewhere left that directory at 0o700, which is a checkout's provisioning
    reaching a path of somebody else's choosing.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(OUTSIDE_MODE)
    (repo / ".logs").symlink_to(outside, target_is_directory=True)

    result = _wrapper(repo, tmp_path / "home")

    assert result.returncode == 1, (
        "a lock directory that was a link out of the checkout was followed rather than "
        f"refused:\n{result.stdout}{result.stderr}"
    )
    assert "is a symbolic link" in result.stderr, (
        f"the refusal did not name what it would not follow:\n{result.stderr}"
    )
    assert "retry" in result.stderr, (
        f"the refusal named nothing an operator does about it:\n{result.stderr}"
    )
    assert stat.S_IMODE(outside.stat().st_mode) == OUTSIDE_MODE, (
        f"{outside} was left at {stat.S_IMODE(outside.stat().st_mode):#o} where it was "
        f"{OUTSIDE_MODE:#o}; this wrapper secured a directory outside the checkout it was "
        "provisioning, chosen by a link it did not create"
    )
    assert not installed_binary(repo).exists(), (
        f"a caller that never established its lock directory still provisioned "
        f"{installed_binary(repo)}"
    )


@pytest.mark.parametrize("target_exists", [False, True])
def test_a_linked_lock_file_is_refused_rather_than_created_outside_the_checkout(
    tmp_path: Path, target_exists: bool
) -> None:
    """A link at the lock path is refused before anything resolves it.

    What that is worth has moved once and the current answer is the one to hold. While
    this wrapper opened the lock read-write, the link was followed by the *open*: a lock
    path linked at a file that did not exist outside the checkout brought that file into
    existence, measured before this refusal existed. The descriptor now asks only to
    read and creates nothing, so what the refusal keeps off a path this wrapper did not
    choose is the read itself — remove it and a link at an existing file outside the
    checkout is opened, and only the name binding afterwards notices.

    Both targets, because the exclusive creation that meets the link takes a different
    route to refusing each: bash's `noclobber` refuses an existing regular file off its
    own `stat`, and reaches `O_EXCL` — which is what refuses the link itself — only when
    that `stat` found nothing. One of those two is the whole of what stands between a
    link planted here and the open below following it.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    outside = tmp_path / "outside-target"
    if target_exists:
        outside.write_text(OUTSIDE_CONTENT, encoding="utf-8")
    logs = repo / ".logs"
    logs.mkdir()
    (logs / "onetaskgraph-install.lock").symlink_to(outside)

    result = _wrapper(repo, tmp_path / "home")

    assert result.returncode == 1, (
        "a lock file that was a link out of the checkout was followed rather than "
        f"refused:\n{result.stdout}{result.stderr}"
    )
    assert "is a symbolic link" in result.stderr, (
        f"the refusal did not name what it would not follow:\n{result.stderr}"
    )
    assert "retry" in result.stderr, (
        f"the refusal named nothing an operator does about it:\n{result.stderr}"
    )
    if target_exists:
        assert outside.read_text(encoding="utf-8") == OUTSIDE_CONTENT, (
            f"{outside} was written through by this wrapper opening its install lock; "
            "the path a checkout's provisioning writes to was decided by a link it did "
            "not create"
        )
    else:
        assert not outside.exists(), (
            f"{outside} was created by this wrapper opening its install lock; the path a "
            "checkout's provisioning writes to was decided by a link it did not create"
        )
    assert not installed_binary(repo).exists(), (
        f"a caller that never established its lock file still provisioned {installed_binary(repo)}"
    )


#: What a file outside the checkout holds before a hard link to it is offered as this
#: wrapper's lock. Worth knowing which half of the journey this is: today's wrapper
#: appends no bytes, so reading it back holds either way and the refusal and the
#: provisioning below are what fail against an unguarded one. This pins the guarantee —
#: the inode is shared, so whatever writes into that lock writes into this file.
OUTSIDE_CONTENT = "bytes this checkout's provisioning has no business opening\n"


@pytest.mark.parametrize("outside_mode", [0o644, 0o444])
def test_a_hard_linked_lock_file_is_refused_rather_than_opened_outside_the_checkout(
    tmp_path: Path, outside_mode: int
) -> None:
    """Being a regular file is not being *this checkout's* file.

    A hard link is a second name for one inode, so it is no link to follow and no
    irregular file — it passes both of the establishing tests beside this one — and
    whatever this wrapper does to the descriptor it does to whatever else names that
    inode. A lock file this wrapper created has exactly one name, so more than one is
    pre-existing state deciding which inode a checkout's provisioning opens.

    Both modes, because the establishing has to happen without asking for authority over
    the inode first. A read-only file outside the checkout is the case that says whether
    it does: opened read-write it is refused by the *open*, as a lock whose permissions
    an operator is told to repair, and the refusal that fits — this inode has another
    name — is never reached. Read-only, both modes reach it, which is what says the
    wrapper established whose file this was before taking anything on it.

    The `curl` on `PATH` serves a *working* archive, which is what makes "provisioning
    did not occur" worth asserting: with a failing one the install cannot happen either
    way, so the assertion holds against a wrapper that never refused at all. Here the
    only thing standing between this caller and a provisioned binary is the refusal.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(
        tmp_path, release_fixture(tmp_path, ADOPTED), name=f"front-hard-linked-{outside_mode:o}"
    )
    outside = tmp_path / "outside-file"
    outside.write_text(OUTSIDE_CONTENT, encoding="utf-8")
    logs = repo / ".logs"
    logs.mkdir()
    os.link(outside, logs / "onetaskgraph-install.lock")
    outside.chmod(outside_mode)

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(front), os.environ["PATH"])),
    )

    assert result.returncode == 1, (
        "a lock file that was a second name for a file outside the checkout was opened "
        f"rather than refused:\n{result.stdout}{result.stderr}"
    )
    assert "names rather than one" in result.stderr, (
        "the refusal did not name what it would not open, so this wrapper asked for "
        f"authority over that inode before it established whose it was:\n{result.stderr}"
    )
    assert "retry" in result.stderr, (
        f"the refusal named nothing an operator does about it:\n{result.stderr}"
    )
    assert outside.read_text(encoding="utf-8") == OUTSIDE_CONTENT, (
        f"{outside} no longer holds what it did; this wrapper opened an inode reachable "
        "outside the checkout it was provisioning, under a name it did not create"
    )
    assert not installed_binary(repo).exists(), (
        f"a caller that never established its lock file still provisioned {installed_binary(repo)}"
    )


#: What the fallback waits in total before it reports a lock nothing is releasing, as
#: the script declares it. A `flock` lock dies with its holder and a directory does not,
#: so only the fallback can inherit one from an install that crashed.
FALLBACK_WAIT_SECONDS = 120


def _sleepless(front: Path) -> None:
    """A `sleep` on ``front`` that returns at once.

    The fallback's whole budget is spent by the journey below on purpose, and spending
    it in real time would be two minutes of a suite doing nothing. What is under test is
    that the budget ends in a refusal naming the directory, not how long a second takes.
    """
    double = front / "sleep"
    double.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    double.chmod(0o755)


def test_the_fallback_serializes_when_the_platform_has_no_flock(tmp_path: Path) -> None:
    """`flock` is util-linux and a stock macOS has none, which this builds a target for.

    So it is preferred rather than required, and the same property the journey above
    proves under `flock` is proven here under the directory the fallback locks with:
    every caller succeeds, and exactly one crosses the fetch boundary.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    home = tmp_path / "home"
    home.mkdir()
    fetched = tmp_path / "fetched"
    serving = fetch_double(
        tmp_path, release_fixture(tmp_path, ADOPTED), delay=FETCH_SECONDS, trace=fetched
    )
    stripped = os.pathsep.join((str(serving), str(path_without(tmp_path, "flock"))))
    environment = {**os.environ, "HOME": str(home), "PATH": stripped}
    # Stated rather than assumed: this journey passes under `flock` too, so without
    # establishing it is gone it would be the journey above wearing a second name.
    resolves = subprocess.run(
        ["bash", "-c", "command -v flock"],
        env={"PATH": stripped},
        text=True,
        capture_output=True,
        check=False,
    )
    assert resolves.returncode != 0, (
        f"this journey's PATH still resolves flock at {resolves.stdout.strip()!r}, so it "
        "would prove the `flock` path a second time rather than the fallback"
    )

    racing = [
        subprocess.Popen(
            ["bash", "scripts/onetaskgraph-install.sh"],
            cwd=repo,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(RACERS)
    ]
    outcomes = [caller.communicate(timeout=180) for caller in racing]

    assert [caller.returncode for caller in racing] == [0] * RACERS, (
        f"a caller racing {RACERS - 1} others with no `flock` on its PATH was refused "
        f"rather than waiting for the directory the fallback locks with:\n{outcomes}"
    )
    reported = subprocess.run(
        [str(installed_binary(repo)), "--version"], text=True, capture_output=True, check=False
    )
    assert reported.stdout.strip() == f"onetaskgraph {ADOPTED}", (
        f"{RACERS} concurrent callers with no `flock` left {reported.stdout.strip()!r} at "
        f"{installed_binary(repo)}, and this checkout adopts {ADOPTED}"
    )
    crossings = fetched.read_text(encoding="utf-8").splitlines()
    assert len(crossings) == FETCHES_PER_INSTALL, (
        f"{len(crossings)} fetches crossed the boundary where one provisioning install "
        f"crosses it {FETCHES_PER_INSTALL} times; the fallback did not serialize:\n{crossings}"
    )
    assert not (repo / ".logs" / "onetaskgraph-install.lock.d").exists(), (
        "the fallback's own lock directory outlived every caller that took it; nothing "
        "releases a directory but the shell that made it, so one left behind is what a "
        "later install inherits and waits out"
    )


def test_the_fallback_refuses_a_lock_directory_swapped_while_it_took_its_mutex(
    tmp_path: Path,
) -> None:
    """The other mechanism creates its lock through that name too, so it is asked too.

    The `flock` build makes a lock *file* under `$lock_dir` and this one makes a mutex
    *directory* there, and neither creation can be bound to the descriptor in portable
    shell. A caller that took either somewhere this wrapper does not hold serializes
    against nothing, so the next install would run concurrently with it — which is the
    one outcome this whole wrapper exists to prevent, and is why it is refused rather
    than installed under.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, None, name="front-swapped-mutex")
    _sleepless(front)
    _swap_the_lock_directory_while_it_is_secured(front, repo, tmp_path)

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(front), str(path_without(tmp_path, "flock")))),
    )

    assert result.returncode == 1, (
        "a fallback that took its mutex in a directory swapped out from under it "
        f"provisioned anyway:\n{result.stdout}{result.stderr}"
    )
    assert "was replaced while this wrapper was taking" in result.stderr, (
        f"the refusal did not name what had been replaced:\n{result.stderr}"
    )
    assert "retry" in result.stderr, (
        f"the refusal named nothing an operator does about it:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a caller holding a mutex outside this checkout still provisioned {installed_binary(repo)}"
    )


def test_the_fallback_refuses_a_lock_that_is_never_released(tmp_path: Path) -> None:
    """A directory outlives whatever made it, so the fallback's wait has to end.

    The case is an install that died holding it: `flock` would have been released by the
    kernel and this cannot be, so the refusal names the directory to remove rather than
    waiting for a process that is gone.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, None, name="front-stale")
    _sleepless(front)
    logs = repo / ".logs"
    logs.mkdir(mode=0o700)
    (logs / "onetaskgraph-install.lock.d").mkdir()

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(front), str(path_without(tmp_path, "flock")))),
    )

    assert result.returncode == 1, (
        f"a lock nothing will ever release was waited on rather than refused:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert f"waited {FALLBACK_WAIT_SECONDS}s" in result.stderr, (
        f"the refusal did not say how long it had waited:\n{result.stderr}"
    )
    assert "remove that directory" in result.stderr, (
        f"the refusal named nothing an operator does about it:\n{result.stderr}"
    )
    assert (logs / "onetaskgraph-install.lock.d").is_dir(), (
        "the refusal removed the lock it was refused by; a caller that never held it is "
        "the one caller that must not release it"
    )
    assert not installed_binary(repo).exists(), (
        f"a caller that never took the install lock still provisioned {installed_binary(repo)}"
    )


def test_the_fallback_parts_a_lock_it_could_not_make_from_one_being_held(
    tmp_path: Path,
) -> None:
    """`mkdir` failing with nothing there failed for its own reason, and waiting is no answer.

    The two are one exit status apart and want opposite things of an operator: wait for
    the other install, or repair a directory this one cannot write in.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, None, name="front-uncreatable")
    _sleepless(front)
    real = shutil.which("mkdir")
    assert real is not None, "this host has no mkdir to defer to"
    double = front / "mkdir"
    double.write_text(
        "#!/usr/bin/env bash\n"
        "for argument; do\n"
        '  case "$argument" in\n'
        "    *.lock.d)\n"
        '      echo "mkdir: cannot create directory: Permission denied" >&2\n'
        "      exit 1\n"
        "      ;;\n"
        "  esac\n"
        "done\n"
        f'exec {shlex.quote(real)} "$@"\n',
        encoding="utf-8",
    )
    double.chmod(0o755)

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(front), str(path_without(tmp_path, "flock")))),
    )

    assert result.returncode == 1, (
        f"a lock that could not be created was waited on rather than refused:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "nothing is holding it" in result.stderr, (
        f"the refusal read as contention when nothing was holding the lock:\n{result.stderr}"
    )
    assert "repair the permissions" in result.stderr, (
        f"the refusal named nothing an operator does about it:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a caller that never took the install lock still provisioned {installed_binary(repo)}"
    )


def test_the_fallback_retries_a_race_it_lost_to_a_peer_that_had_already_released(
    tmp_path: Path,
) -> None:
    """A `mkdir` that failed with nothing at the path lost a race far more often than it faulted.

    The winner releases the directory when its shell ends, so a loser that looks after
    that release finds nothing there — the same appearance the journey above builds for a
    `mkdir` that cannot create at all. Refusing on the first sight of it fails a caller
    that had only to try again, which is what a loaded host turned into a gate failure
    here: three of four racers took the lock and the fourth was told to repair permissions
    nobody had broken. So an absence has to persist before it is read as a fault, and this
    drives the transient one — the first attempt fails with the path absent, and the
    caller provisions rather than refusing.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, release_fixture(tmp_path, ADOPTED), name="front-transient")
    _sleepless(front)
    real = shutil.which("mkdir")
    assert real is not None, "this host has no mkdir to defer to"
    lost = tmp_path / "lost-the-race-once"
    double = front / "mkdir"
    double.write_text(
        "#!/usr/bin/env bash\n"
        "for argument; do\n"
        '  case "$argument" in\n'
        "    *.lock.d)\n"
        f"      if [ ! -e {shlex.quote(str(lost))} ]; then\n"
        f"        : >{shlex.quote(str(lost))}\n"
        '        echo "mkdir: cannot create directory: File exists" >&2\n'
        "        exit 1\n"
        "      fi\n"
        "      ;;\n"
        "  esac\n"
        "done\n"
        f'exec {shlex.quote(real)} "$@"\n',
        encoding="utf-8",
    )
    double.chmod(0o755)

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(front), str(path_without(tmp_path, "flock")))),
    )

    # Asserted rather than assumed: a double that never refused would make this the
    # ordinary acquisition wearing a second name, and it would pass for that reason.
    assert lost.is_file(), (
        "the double never refused a `.lock.d` creation, so nothing here drove the lost "
        "race this journey is about"
    )
    assert result.returncode == 0, (
        f"a caller whose first `mkdir` lost the race was refused rather than retrying:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "nothing is holding it" not in result.stderr, (
        f"a lost race was reported as a lock that could not be created:\n{result.stderr}"
    )
    assert installed_binary(repo).is_file(), (
        f"the caller took the lock on its retry and still provisioned nothing at "
        f"{installed_binary(repo)}"
    )


#: What a lock directory somebody else left too open is found at, and what provisioning
#: must leave it at. `chmod` is reached only from here — a directory this wrapper makes
#: is 700 from `mkdir` — so this is the one journey that drives it at all.
PERMISSIVE_MODE = 0o755


def test_an_existing_lock_directory_is_secured_before_the_install_runs(tmp_path: Path) -> None:
    """The descriptor-bound `chmod`, driven where it actually happens.

    Every other journey over this wrapper either creates `.logs` at 700 or is refused
    before reaching it, so without this one the securing step is only ever asserted by
    the refusal that stands in for its failure.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, release_fixture(tmp_path, ADOPTED), name="front-permissive")
    logs = repo / ".logs"
    logs.mkdir(mode=PERMISSIVE_MODE)

    result = _wrapper(
        repo, tmp_path / "home", PATH=os.pathsep.join((str(front), os.environ["PATH"]))
    )

    assert result.returncode == 0, (
        f"a lock directory left at {PERMISSIVE_MODE:#o} was refused rather than secured:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700, (
        f"{logs} was left at {stat.S_IMODE(logs.stat().st_mode):#o}; this wrapper writes "
        "its install lock in that directory and did not restrict who else may read it"
    )
    assert installed_binary(repo).is_file(), (
        f"securing the lock directory left nothing provisioned at {installed_binary(repo)}"
    )


@pytest.mark.parametrize("occupant", ["file", "symlink"])
def test_the_fallback_refuses_a_lock_path_no_install_could_have_taken(
    tmp_path: Path, occupant: str
) -> None:
    """Only a directory at that path is an install holding the lock.

    Anything else is something waiting cannot resolve, and waiting the budget out would
    report it as a lock that was never released — the one repair that cannot help, since
    no install ever made it.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, None, name=f"front-occupied-{occupant}")
    _sleepless(front)
    logs = repo / ".logs"
    logs.mkdir(mode=0o700)
    occupied = logs / "onetaskgraph-install.lock.d"
    if occupant == "file":
        occupied.write_text("not a lock\n", encoding="utf-8")
    else:
        occupied.symlink_to(tmp_path / "elsewhere")

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(front), str(path_without(tmp_path, "flock")))),
    )

    assert result.returncode == 1, (
        f"a lock path no install could have taken was waited on rather than refused:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "it is not a directory" in result.stderr, (
        f"the refusal did not name what was wrong with that path:\n{result.stderr}"
    )
    assert f"waited {FALLBACK_WAIT_SECONDS}s" not in result.stderr, (
        "the refusal reported a lock that was never released, which is the one repair "
        f"that cannot help when no install ever took it:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a caller that never took the install lock still provisioned {installed_binary(repo)}"
    )


def test_the_fallback_refuses_a_wait_it_cannot_perform(tmp_path: Path) -> None:
    """A `sleep` that fails turns the fallback's wait into a spin, so it refuses instead.

    The poll is the whole of how this fallback waits, so a wait that cannot happen is
    not contention and says so — the repair is the command, not the other install.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, None, name="front-sleepless")
    refusing = front / "sleep"
    refusing.write_text(
        '#!/usr/bin/env bash\necho "sleep: cannot wait" >&2\nexit 1\n', encoding="utf-8"
    )
    refusing.chmod(0o755)
    logs = repo / ".logs"
    logs.mkdir(mode=0o700)
    (logs / "onetaskgraph-install.lock.d").mkdir()

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(front), str(path_without(tmp_path, "flock")))),
    )

    assert result.returncode == 1, (
        f"a wait that could not happen span rather than refusing:\n{result.stdout}{result.stderr}"
    )
    assert "because sleep failed" in result.stderr, (
        f"the refusal did not name what it could not do:\n{result.stderr}"
    )
    assert "repair that command" in result.stderr, (
        f"the refusal pointed at the other install rather than at the command:\n{result.stderr}"
    )
    assert not installed_binary(repo).exists(), (
        f"a caller that never took the install lock still provisioned {installed_binary(repo)}"
    )


def test_the_fallback_reports_a_lock_it_could_not_release(tmp_path: Path) -> None:
    """A lock left behind is the next install's whole wait, so a failed release is said.

    It is not this install's failure — the work is done and the binary is provisioned —
    which is exactly why nothing else would ever mention it.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, release_fixture(tmp_path, ADOPTED), name="front-unreleasable")
    refusing = front / "rmdir"
    refusing.write_text(
        '#!/usr/bin/env bash\necho "rmdir: cannot remove" >&2\nexit 1\n', encoding="utf-8"
    )
    refusing.chmod(0o755)

    result = _wrapper(
        repo,
        tmp_path / "home",
        PATH=os.pathsep.join((str(front), str(path_without(tmp_path, "flock")))),
    )

    assert result.returncode == 0, (
        f"a release that failed was reported as an install that failed:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert "could not release the install lock" in result.stderr, (
        f"a lock left behind was not reported at all:\n{result.stderr}"
    )
    assert f"waits {FALLBACK_WAIT_SECONDS}s" in result.stderr, (
        f"the report did not say what it costs the next install:\n{result.stderr}"
    )
    assert installed_binary(repo).is_file(), (
        f"the install itself did not finish, so this proves nothing about its release: "
        f"{installed_binary(repo)}"
    )


def test_the_bsd_stat_form_answers_where_the_gnu_one_is_refused(tmp_path: Path) -> None:
    """`stat -c` is GNU and `stat -f` is BSD, and a macOS host has only the second.

    Asked in turn rather than chosen by platform, so the branch that carries a Darwin
    install is the second attempt — which on this host never runs, because the first
    one answers. A `stat` that refuses the GNU form is what a macOS one is from here.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, release_fixture(tmp_path, ADOPTED), name="front-bsd-stat")
    answered = tmp_path / "answered"
    double = front / "stat"
    double.write_text(
        "#!/usr/bin/env bash\n"
        "# A macOS stat: it has no -c, and its -f prints its own capitalisation.\n"
        "if [[ $2 == -c ]]; then\n"
        '  echo "stat: illegal option -- c" >&2\n'
        "  exit 1\n"
        "fi\n"
        f'printf "%s\\n" "${{@: -1}}" >>{shlex.quote(str(answered))}\n'
        'case "${@: -1}" in\n'
        "  */fd/8) printf 'Directory 700\\n' ;;\n"
        "  */fd/9) printf 'Regular File 1\\n' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    double.chmod(0o755)

    result = _wrapper(
        repo, tmp_path / "home", PATH=os.pathsep.join((str(front), os.environ["PATH"]))
    )

    assert result.returncode == 0, (
        "a host whose stat answers only the BSD form could not provision:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert answered.is_file(), (
        "the BSD form was never reached, so this drove the GNU branch a second time "
        "rather than the one a macOS install depends on"
    )
    descriptors = sorted(set(answered.read_text(encoding="utf-8").split()))
    assert descriptors == ["/dev/fd/8", "/dev/fd/9"], (
        f"the BSD form answered for {descriptors} where this wrapper reads back the lock "
        "directory's descriptor and the lock file's; a form that answered for neither "
        "would leave the capitalisation it returns untested"
    )
    assert installed_binary(repo).is_file(), (
        f"the BSD stat form left nothing provisioned at {installed_binary(repo)}"
    )


#: The two expansions this wrapper deliberately stopped using, and what each replaced
#: them with. Not a model of what bash 3.2 rejects — that is bash's grammar and this
#: restates none of it — but a record of a decision made in this file: `${x,,}` folded
#: `UV_NO_SYNC` before matching it and `${answer,,}` folded `stat`'s own capitalisation,
#: both were replaced when the `flock` fallback below them made 3.2 a platform this has
#: to parse on, and either one coming back is that decision being undone.
#: What would settle the general question is running the file under bash 3.2, which no
#: host here has; nothing below claims to stand in for that.
FOLDING_EXPANSIONS_REPLACED = (
    (
        r"\$\{[A-Za-z_][A-Za-z_0-9]*(\[[^]]*\])?,,?\}",
        "${var,,}, replaced by case-insensitive globs and by `tr`",
    ),
    (
        r"\$\{[A-Za-z_][A-Za-z_0-9]*(\[[^]]*\])?\^\^?\}",
        "${var^^}, the same operator the other way up",
    ),
)


def test_the_wrapper_still_folds_without_the_expansions_it_gave_up() -> None:
    """The `flock` fallback exists for stock macOS, whose bash is 3.2.

    Both places this file folded a value were rewritten for that, because either
    expansion is a parse error there — one that takes the whole file rather than its own
    branch, so the fallback would not run either. This holds the rewrite rather than the
    shell: what the file must do on 3.2 is answered by the journeys below, which drive
    the folding it kept; what it must not contain is here.

    Comments are cut before matching, since the two that explain this name the operators.
    """
    wrapper = REPO_ROOT / "scripts" / "onetaskgraph-install.sh"
    code = "\n".join(
        "" if line.lstrip().startswith("#") else line
        for line in wrapper.read_text(encoding="utf-8").splitlines()
    )

    found = [
        (name, code[: match.start()].count("\n") + 1)
        for pattern, name in FOLDING_EXPANSIONS_REPLACED
        for match in re.finditer(pattern, code)
    ]

    assert found == [], (
        f"{wrapper} uses {found}. That expansion was given up when the `flock` fallback "
        "made bash 3.2 a platform this file has to parse on, and 3.2 cannot parse it — "
        "so the fallback it was given up for is what stops running"
    )


@pytest.mark.parametrize(
    ("value", "provisions"),
    [
        ("TRUE", False),
        ("True", False),
        ("yEs", False),
        ("ON", False),
        ("Y", False),
        ("FALSE", True),
        ("No", True),
        ("oFF", True),
        ("N", True),
    ],
)
def test_the_provided_environment_signal_is_read_whatever_its_case(
    tmp_path: Path, value: str, provisions: bool
) -> None:
    """uv reads this signal case-insensitively, and so must this wrapper.

    It used to fold the value with `${UV_NO_SYNC,,}`, which bash 3.2 cannot parse; the
    case-insensitive globs that replaced it have to accept exactly what the folding did.
    Only mixed-case spellings are asked here — the lowercase ones the folding produced
    are covered above, and they would pass a wrapper that had stopped folding at all.
    """
    repo = checkout(tmp_path / "repo", version=ADOPTED)
    front = fetch_double(tmp_path, release_fixture(tmp_path, ADOPTED), name=f"front-{value}")

    result = _wrapper(
        repo,
        tmp_path / "home",
        UV_NO_SYNC=value,
        PATH=os.pathsep.join((str(front), os.environ["PATH"])),
    )

    assert result.returncode == 0, (
        f"UV_NO_SYNC={value!r} was refused as a value uv does not read:\n"
        f"{result.stdout}{result.stderr}"
    )
    assert installed_binary(repo).is_file() is provisions, (
        f"UV_NO_SYNC={value!r} "
        f"{'left nothing provisioned' if provisions else 'installed over an environment'}"
        f" at {installed_binary(repo)}; uv reads that value as "
        f"{'provision this checkout' if provisions else 'keep what I gave you'}"
    )


#: One arm of a `case` over `UV_NO_SYNC`, in either spelling the two wrappers use:
#: `python-install.sh` folds the value first and lists it lowercase, and this wrapper
#: cannot fold — bash 3.2 — so it lists case-insensitive globs instead.
_NO_SYNC_ARM = re.compile(
    r"^\s*(?P<spellings>[A-Za-z0-9\[\]]+(?: \| [A-Za-z0-9\[\]]+)*)\)(?P<body>.*);;\s*$"
)


def _no_sync_grammar(script: Path) -> dict[str, bool]:
    """Each `UV_NO_SYNC` spelling ``script`` accepts, mapped to whether it skips.

    Read out of the script rather than restated. Glob classes are folded back to the
    letter they match — `[Tt][Rr][Uu][Ee]` is `true` — so the two wrappers' arms are
    comparable however each had to spell them.
    """
    grammar = {
        re.sub(r"\[(\w)(\w)\]", lambda letter: letter[2].lower(), spelling): "exit 0" in arm["body"]
        for line in script.read_text(encoding="utf-8").splitlines()
        if (arm := _NO_SYNC_ARM.match(line)) is not None
        for spelling in arm["spellings"].split(" | ")
    }
    if not grammar:
        raise AssertionError(f"{script} no longer names its UV_NO_SYNC grammar")
    return grammar


def test_both_provisioning_wrappers_read_the_same_provided_environment_signal() -> None:
    """`UV_NO_SYNC` is uv's, and two wrappers here answer to it.

    `scripts/python-install.sh` is where that grammar is declared and where the journey
    that measures it against uv reads it from. This wrapper cannot share the spelling —
    folding the value is bash 4.0 and its `flock` fallback needs 3.2 — so it carries its
    own arms, and two copies of one grammar is a thing that drifts. What is held is that
    they mean the same, not that they read the same: the globs are folded back before
    they are compared.
    """
    declared = _no_sync_grammar(REPO_ROOT / "scripts" / "python-install.sh")
    ours = _no_sync_grammar(REPO_ROOT / "scripts" / "onetaskgraph-install.sh")

    assert ours == declared, (
        f"scripts/onetaskgraph-install.sh reads UV_NO_SYNC as {ours} where "
        f"scripts/python-install.sh declares {declared}. Both provision part of this "
        "checkout's toolchain from one invocation, so a value that keeps one environment "
        "and replaces the other is uv's own signal meaning two things here"
    )
