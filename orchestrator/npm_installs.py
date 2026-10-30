"""Installed npm commands and platform artifacts for binary and SBOM inspection."""

from __future__ import annotations

import platform
from pathlib import Path

from orchestrator.root import REPO_ROOT

__all__ = [
    "ENGINE",
    "NODE_MODULES",
    "PLATFORMS",
    "READ_API",
    "platform_binary",
    "installed_command",
]

#: Where `bun.lock` installs this checkout's npm packages.
NODE_MODULES = REPO_ROOT / "node_modules"

#: Node's `<process.platform>-<process.arch>` for each host Python names, which is the
#: suffix of the platform package npm installs there: the four the producers publish.
PLATFORMS = {
    ("Linux", "x86_64"): "linux-x64",
    ("Linux", "aarch64"): "linux-arm64",
    ("Darwin", "arm64"): "darwin-arm64",
    ("Windows", "AMD64"): "win32-x64",
}


def platform_binary(
    package: str = "onepipeline-cli",
    binary: str = "onepipeline",
    *,
    node_modules: Path = NODE_MODULES,
    host: tuple[str, str] | None = None,
) -> Path:
    """The prebuilt `binary` `package`'s platform package carries for ``host``.

    ``host`` is ``(platform.system(), platform.machine())`` unless named. A host no
    platform package is published for is refused by name rather than guessed at.
    """
    named = host or (platform.system(), platform.machine())
    if named not in PLATFORMS:
        raise LookupError(
            f"{package} publishes no platform package for {named}; it publishes "
            f"{sorted(PLATFORMS.values())}"
        )
    return node_modules / f"{package}-{PLATFORMS[named]}" / "bin" / binary


def installed_command(
    package: str = "onepipeline-cli",
    binary: str = "onepipeline",
    *,
    node_modules: Path = NODE_MODULES,
) -> Path:
    """The command installed by the npm package, relative to its checkout."""
    return node_modules / ".bin" / binary


#: The engine command every module here runs.
ENGINE = installed_command()
#: The read API's binary, which `just dag-ui` and `just telemetry-server` run.
READ_API = installed_command("onepipeline-api-cli", "onepipeline-api")
