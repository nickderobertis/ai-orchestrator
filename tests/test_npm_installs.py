"""Where `bun.lock` installs the engine and the read API, read off this checkout's install."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator import npm_installs
from orchestrator.root import REPO_ROOT


def test_the_binaries_are_this_checkouts_platform_packages() -> None:
    """Each binary every caller runs is inside this checkout's platform package for it.

    That the installed binary answers its pin, and as the npm launcher beside it does, is
    the `session-setup-pypi` journey's to show on a real install, because running a
    binary is a host tool this offline tier does not pay for.
    """
    for binary, package in (
        (npm_installs.ENGINE, "onepipeline-cli"),
        (npm_installs.READ_API, "onepipeline-api-cli"),
    ):
        assert binary.parent.parent.parent == REPO_ROOT / "node_modules"
        assert binary.parent.parent.name.startswith(f"{package}-")


@pytest.mark.parametrize(
    ("package", "binary", "resolved"),
    [
        ("onepipeline-cli", "onepipeline", npm_installs.ENGINE),
        ("onepipeline-api-cli", "onepipeline-api", npm_installs.READ_API),
    ],
)
def test_the_scripts_resolve_the_binary_this_module_does(
    package: str, binary: str, resolved: Path
) -> None:
    """`scripts/npm-binary.sh`, which every script runs the binaries through, agrees here."""
    printed = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1" && npm_binary "$2" "$3"',
            "-",
            "scripts/npm-binary.sh",
            package,
            binary,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert printed.stdout == f"{resolved}\n"


@pytest.mark.parametrize("package", ["onepipeline-cli", "onepipeline-api-cli"])
def test_the_platform_table_is_every_platform_package_the_lock_resolves(package: str) -> None:
    """DRIFT-GATE `PLATFORMS` against the producer's own platform packages in `bun.lock`.

    The suffixes are the producer's to publish, so a mapping for a host this one is not is
    held to the `optionalDependencies` the locked release declares, not to itself.
    """
    text = (REPO_ROOT / "bun.lock").read_text(encoding="utf-8")
    # Bun writes JSON with trailing commas.
    locked = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))["packages"][package]
    declared = locked[2]["optionalDependencies"]
    suffixes = {name.removeprefix(f"{package}-") for name in declared}
    assert all(name.startswith(f"{package}-") for name in declared), declared
    assert suffixes == set(npm_installs.PLATFORMS.values()), (
        f"{package}'s locked release publishes platform packages for {sorted(suffixes)}, "
        f"and orchestrator/npm_installs.py maps {sorted(npm_installs.PLATFORMS.values())}"
    )


@pytest.mark.parametrize(
    ("host", "suffix"),
    [(host, suffix) for host, suffix in npm_installs.PLATFORMS.items()],
)
def test_each_published_platform_names_its_package(host: tuple[str, str], suffix: str) -> None:
    found = npm_installs.platform_binary(
        "onepipeline-api-cli", "onepipeline-api", node_modules=Path("/nm"), host=host
    )
    assert found == Path(f"/nm/onepipeline-api-cli-{suffix}/bin/onepipeline-api")


@pytest.mark.parametrize(
    "host",
    [host for host in npm_installs.PLATFORMS if host[0] != "Windows"],
    ids=lambda host: "-".join(host),
)
def test_the_scripts_map_every_host_as_this_module_does(
    tmp_path: Path, host: tuple[str, str]
) -> None:
    """DRIFT-GATE `scripts/npm-binary.sh`'s host table against `PLATFORMS`, host by host.

    Each host `uname` can name for a shell is put to the script, so a mapping that parts
    from this module's for a host this one is not fails here rather than on that host.
    Windows runs no bash script here, so its row is this module's alone.
    """
    system, machine = host
    uname = tmp_path / "uname"
    uname.write_text(
        f'#!/bin/sh\ncase "$1" in -s) echo {system} ;; -m) echo {machine} ;; esac\n',
        encoding="utf-8",
    )
    uname.chmod(0o755)
    printed = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1" && npm_binary onepipeline-cli onepipeline',
            "-",
            "scripts/npm-binary.sh",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=True,
    )
    assert Path(printed.stdout.strip()) == npm_installs.platform_binary(host=host)


def test_a_host_no_platform_package_is_published_for_is_refused_by_name() -> None:
    with pytest.raises(LookupError, match=r"onepipeline-cli publishes no platform package"):
        npm_installs.platform_binary(host=("Darwin", "x86_64"))


def test_the_scripts_refuse_a_host_no_platform_package_is_published_for(tmp_path: Path) -> None:
    """A host `uname` names outside the four is refused by name, printing no path."""
    uname = tmp_path / "uname"
    uname.write_text(
        '#!/bin/sh\ncase "$1" in -s) echo Darwin ;; -m) echo x86_64 ;; esac\n', encoding="utf-8"
    )
    uname.chmod(0o755)
    refused = subprocess.run(
        [
            "bash",
            "-c",
            '. "$1" && npm_binary onepipeline-cli onepipeline',
            "-",
            "scripts/npm-binary.sh",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )
    assert refused.returncode == 2, refused
    assert refused.stdout == ""
    assert "onepipeline-cli publishes no platform package for Darwin x86_64" in refused.stderr


def test_run_as_a_command_it_explains_a_binary_the_kernel_will_not_run(tmp_path: Path) -> None:
    """An installed binary that cannot be executed is reported with its remedy, not left bare."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(REPO_ROOT / "scripts" / "npm-binary.sh", scripts)
    binary = npm_installs.platform_binary(node_modules=tmp_path / "node_modules")
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    binary.chmod(0o755)

    ran = subprocess.run(
        ["bash", str(scripts / "npm-binary.sh"), "onepipeline-cli", "onepipeline", "--version"],
        text=True,
        capture_output=True,
    )

    assert ran.returncode == 126, ran
    assert f"npm-binary: {binary} is executable but could not be run" in ran.stderr
    assert "reinstall it with 'just bootstrap'" in ran.stderr
