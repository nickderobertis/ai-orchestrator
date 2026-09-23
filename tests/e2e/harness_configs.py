"""A role config's `extends` chain: how to read one, and how to copy one that resolves.

Each `oneharness.<role>.toml` at the repository root states its own chain, deadline and
tier and inherits the six identities from `oneharness.identities.toml`, directly or
through `oneharness.dispatch.toml`. Two consequences reach every journey that touches
one, and both are why the walk lives here rather than in each module that needs it:

* reading a role file's text finds that role's own decisions and none of the identities,
  so a question about a variant is a question about the whole chain;
* `extends` names a path relative to the directory of the file DECLARING it — the
  released core's own rule for that key — so a role config copied anywhere on its own
  resolves nothing, and `oneharness` refuses it by name.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import TypedDict, cast


class Variant(TypedDict, total=False):
    """One authentication variant of a harness, narrowed to what this suite reads.

    `env_from` maps the variable the variant sets to the variable it reads that value
    out of the parent process, and `oneharness` refuses to start the variant when the
    second one is unset.
    """

    env_from: dict[str, str]


class Harness(TypedDict, total=False):
    """One harness's routing, narrowed to its variants."""

    variant: dict[str, Variant]


class HarnessRouting(TypedDict, total=False):
    """One oneharness config, narrowed to the three keys this suite reads.

    `oneharness` owns the rest of the schema and is what validates it; these are the
    keys a journey here consults — the variants whose indirections have to exist, the
    identity order a chain declares, and the parent a role config inherits its
    identities from.
    """

    extends: str
    harness: dict[str, Harness]
    harnesses: list[str]


def harness_routing(config: Path) -> HarnessRouting:
    """One oneharness config, as the CLI that reads it lays it out.

    `cast` rather than a validating read: the file is this repository's own and
    `oneharness` is what holds it to its schema, so `HarnessRouting` states the shape
    consulted here instead of restating somebody else's validation.
    """
    return cast(HarnessRouting, tomllib.loads(config.read_text(encoding="utf-8")))


def extends_chain(config: Path) -> tuple[Path, ...]:
    """`config` itself, then each parent its `extends` names, nearest first."""
    chain: list[Path] = []
    seen: set[Path] = set()
    current = config.resolve()
    while current not in seen:
        seen.add(current)
        chain.append(current)
        parent = harness_routing(current).get("extends")
        if parent is None:
            return tuple(chain)
        current = (current.parent / parent).resolve()
    raise AssertionError(f"{config} extends itself through {[path.name for path in chain]}")


def copied_with_its_parents(config: Path, destination: Path, *, name: str | None = None) -> Path:
    """Copy `config` into `destination` with the parents it extends, and return the copy.

    For a journey that builds a config of its own out of this repository's real routing:
    what it runs is the committed chain rather than a handwritten stand-in, and it can
    edit its copy without touching the tree. Each parent keeps its own file name, which
    is how the `extends` line that reaches it names it; only the role config may be
    renamed, and nothing names it but the caller.
    """
    destination.mkdir(parents=True, exist_ok=True)
    role, *parents = extends_chain(config)
    copy = destination / (name or role.name)
    shutil.copyfile(role, copy)
    for parent in parents:
        shutil.copyfile(parent, destination / parent.name)
    return copy
