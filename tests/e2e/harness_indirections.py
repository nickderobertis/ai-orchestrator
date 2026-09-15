"""The alternate-identity indirections a journey has to establish before it runs.

`oneharness` refuses to start a variant whose `env_from` indirection is unset, so a
journey that reaches a **real** oneharness — every one whose member is single-sided
`kind: oneharness`, which runs its turn through the library rather than through a
substituted CLI — has to carry `ORCHESTRATOR_CODEX_ALT_HOME` and every
`ORCHESTRATOR_CLAUDE_*_CONFIG_DIR` value.

A provisioned planner session and a dispatched worker both carry them already, from
`scripts/session-setup.sh`; nothing on the `just gate` path does. Inheriting them
made those journeys pass on hosts that had run the session hook and fail everywhere
else — a bare shell, a fresh terminal, a CI job — with an assertion about something
else entirely as the only symptom.

They are derived here through the helpers that own them rather than assembled from
paths, and which variables to derive is read out of the configs that name them, so a
config naming a new one reaches every journey at once instead of failing one of them
with the environment out of sight again.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT


class IndirectionSource(NamedTuple):
    """One helper and the function it defines, which are not interchangeable.

    Sourcing a helper sets nothing — a caller must invoke its function — so the two
    travel together, named rather than positional.
    """

    #: The helper's repository-relative path, sourced for its definitions.
    helper: str
    #: The function that derives, validates, and exports what the helper is for.
    function: str


class Indirection(NamedTuple):
    """One established indirection, as the helper that owns it resolved it."""

    name: str
    value: str


#: Each alternate-identity indirection's ONE source: the helpers every wrapper that
#: reaches `oneharness` sources. Deriving the paths here instead would be the second
#: copy those files exist to prevent.
INDIRECTION_SOURCES = (
    IndirectionSource("scripts/codex-alt-home.sh", "ensure_codex_alt_home"),
    IndirectionSource("scripts/claude-alt-config-dir.sh", "resolve_claude_alt_config_dir"),
)

#: The configs whose members run oneharness in-library, so their variants resolve from
#: this process's environment rather than a spawned CLI's.
SINGLE_SIDED_CONFIGS = (
    REPO_ROOT / "oneharness.check-in.toml",
    REPO_ROOT / "oneharness.pr-author.toml",
)


class Variant(TypedDict, total=False):
    """One authentication variant of a harness, narrowed to what is read here.

    `env_from` is the indirection this module exists for: it maps the variable the
    variant sets to the variable it reads that value out of the parent process, and
    `oneharness` refuses to start the variant when the second one is unset.
    """

    env_from: dict[str, str]


class Harness(TypedDict, total=False):
    """One harness's routing, narrowed to its variants."""

    variant: dict[str, Variant]


class HarnessRouting(TypedDict, total=False):
    """One oneharness config, narrowed to the two tables this suite reads.

    `oneharness` owns the rest of the schema and is what validates it; these are the
    keys a journey here consults — the variants whose indirections have to exist, and
    the identity order a chain declares.
    """

    harness: dict[str, Harness]
    harnesses: list[str]


def harness_routing(config: Path) -> HarnessRouting:
    """One oneharness config, as the CLI that reads it lays it out.

    `cast` rather than a validating read: the file is this repository's own and
    `oneharness` is what holds it to its schema, so `HarnessRouting` states the shape
    consulted here instead of restating somebody else's validation.
    """
    return cast(HarnessRouting, tomllib.loads(config.read_text(encoding="utf-8")))


def _configured_indirections() -> tuple[str, ...]:
    """Every variable a variant's `env_from` reads out of the parent process.

    Derived rather than listed: `oneharness` refuses to start a variant whose
    indirection is unset, so a config that names a new one has to reach the journeys
    below or they would fail on it with the environment out of sight again.
    """
    named = {
        variable
        for config in SINGLE_SIDED_CONFIGS
        for harness in harness_routing(config).get("harness", {}).values()
        for variant in harness.get("variant", {}).values()
        for variable in variant.get("env_from", {}).values()
    }
    return tuple(sorted(named))


#: A shell variable name, which is what an indirection has to be to be read back out
#: of one. A config declaring anything else is refused below rather than interpolated
#: into the program that reads them.
SHELL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: How the caller's own name reaches the helpers: an environment variable rather than
#: text spliced into the program, so no value a caller passes is ever shell syntax.
CALLER_ENV = "ORCHESTRATOR_INDIRECTION_CALLER"

INDIRECTIONS = _configured_indirections()


@functools.cache
def established_indirections(caller: str) -> tuple[Indirection, ...]:
    """Establish the alternate-identity indirections through their own one source.

    `caller` is the module that will fail without them, and is what the helpers
    attribute their own diagnostics to — the whole point being that a missing
    indirection stops reading as somebody else's broken assertion.

    Cached because the answer is the host's and every journey that needs them asks
    for the same one.
    """
    malformed = [name for name in INDIRECTIONS if SHELL_NAME.fullmatch(name) is None]
    assert not malformed, (
        f"{', '.join(malformed)} is named as an `env_from` indirection but is not a shell "
        f"variable name, so it could not be read back out of one: fix the declaration in "
        f"{', '.join(config.name for config in SINGLE_SIDED_CONFIGS)}"
    )
    script = "\n".join(
        [
            *(
                f". {shlex.quote(str(REPO_ROOT / source.helper))}\n"
                f'{source.function} "${CALLER_ENV}"'
                for source in INDIRECTION_SOURCES
            ),
            *(f'printf "%s=%s\\n" {name} "${{{name}-}}"' for name in INDIRECTIONS),
        ]
    )
    resolved = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, CALLER_ENV: caller},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    established = {
        name: value
        for name, _, value in (line.partition("=") for line in resolved.stdout.splitlines())
        if value
    }
    unestablished = [name for name in INDIRECTIONS if name not in established]
    if unestablished:
        pytest.fail(
            f"{caller} could not establish {', '.join(unestablished)} from "
            f"{', '.join(source.helper for source in INDIRECTION_SOURCES)}; `oneharness` "
            f"refuses to start a variant whose indirection is unset:\n{resolved.stderr}"
        )
    return tuple(Indirection(name, established[name]) for name in INDIRECTIONS)
