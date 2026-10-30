"""Where `bun.lock` installs the engine and the read API, read off this checkout's install."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from orchestrator import npm_installs
from orchestrator.root import REPO_ROOT


def test_the_commands_are_this_checkouts_installed_commands() -> None:
    for command, name in (
        (npm_installs.ENGINE, "onepipeline"),
        (npm_installs.READ_API, "onepipeline-api"),
    ):
        assert command == REPO_ROOT / "node_modules" / ".bin" / name


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


def test_a_host_no_platform_package_is_published_for_is_refused_by_name() -> None:
    with pytest.raises(LookupError, match=r"onepipeline-cli publishes no platform package"):
        npm_installs.platform_binary(host=("Darwin", "x86_64"))
