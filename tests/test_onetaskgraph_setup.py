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
import subprocess
import tarfile
from pathlib import Path

import pytest
from onetaskgraph_release import (
    FIXTURE_TARGET,
    checkout,
    fetch_double,
    installed_binary,
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


def _wrapper(repo: Path, home: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    """Run `scripts/onetaskgraph-install.sh` in ``repo`` and report what it said.

    Nothing is doubled: every journey below is about a refusal the wrapper reaches
    before it would fetch anything, so there is no boundary to stand in for.
    """
    return subprocess.run(
        ["bash", "scripts/onetaskgraph-install.sh", *environment.pop("arguments", "").split()],
        cwd=repo,
        env={**os.environ, "HOME": str(home), **environment},
        text=True,
        capture_output=True,
        check=False,
    )


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
