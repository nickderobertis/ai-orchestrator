"""Where this checkout's `bun.lock` installs the engine and the read API from npmjs.com.

`onepipeline-cli` and `onepipeline-api-cli` are npm packages whose launcher, in
`node_modules/.bin`, is a node script that resolves and runs the prebuilt binary the
platform package npm installed for this host — `onepipeline-cli-linux-x64` on this one —
and that platform package ships the release's CycloneDX SBOM under `sboms/` beside it.

Every caller here runs that binary itself, never the launcher, for the reason
`scripts/npm-binary.sh` gives, and the two retire together.
"""

from __future__ import annotations

import platform
from pathlib import Path

from orchestrator.root import REPO_ROOT

__all__ = ["ENGINE", "NODE_MODULES", "PLATFORMS", "READ_API", "platform_binary"]

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


#: The engine's binary every module here runs.
ENGINE = platform_binary()
#: The read API's binary, which `just dag-ui` and `just telemetry-server` run.
READ_API = platform_binary("onepipeline-api-cli", "onepipeline-api")
