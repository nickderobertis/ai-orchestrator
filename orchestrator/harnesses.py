"""Per-side harness selection: which provider does the work, which one supervises.

onejudge routes BOTH conversation sides through one ``provider.bin``, and
oneharness's own ``ONEHARNESS_HARNESSES`` is process-wide *and* beats config — so
setting it to put the worker on codex silently drags the judge onto codex too.
These two variables are the seam that keeps the choice per side:
``scripts/oneharness-agent.sh`` resolves the worker one on its agent branch and
the judge one on its judge branch, and each applies to only that branch's own
``exec``. A side given its own value therefore never inherits the other side's,
nor a process-wide value the parent happened to export.

An override names identities exactly as a config's ``harnesses`` chain does
(``codex``, ``codex:alternate``, ``claude-code:alternate2``), comma-separated for
a fallback chain of its own. It is validated **here**, before anything is
dispatched, against that side's own config: a run that quietly used a different
provider than it was told to is worse than one that refused to start.

The two configs are the one source of what is selectable, so an operator can
reorder or narrow a chain but cannot name an identity whose credentials, model,
and environment routing this repository has not configured.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import REPO_ROOT
from .config import ConfigError

#: The worker side's selection, read by the wrapper's agent branch.
WORKER_HARNESS_ENV = "ORCHESTRATOR_WORKER_HARNESSES"
#: The judge / simulated-user side's selection, read by the wrapper's judge branch.
JUDGE_HARNESS_ENV = "ORCHESTRATOR_JUDGE_HARNESSES"


@dataclass(frozen=True)
class HarnessSide:
    """One conversation side: the flag that sets it, the config and env that carry it."""

    name: str
    option: str
    env: str
    config: Path


WORKER_SIDE = HarnessSide(
    name="worker",
    option="--worker-harness",
    env=WORKER_HARNESS_ENV,
    config=REPO_ROOT / "oneharness.toml",
)
JUDGE_SIDE = HarnessSide(
    name="judge",
    option="--judge-harness",
    env=JUDGE_HARNESS_ENV,
    config=REPO_ROOT / "oneharness.judge.toml",
)


def harness_option_help(side: HarnessSide) -> str:
    """One source for the option help every dispatching entry point offers."""
    return (
        f"harness identity running the {side.name} side of the conversation "
        f"(via {side.env}), comma-separated for a fallback chain of its own; "
        f"selected from {side.config.name}'s chain, which is also the default"
    )


def configured_harnesses(config: Path) -> tuple[str, ...]:
    """The identities that config's ``harnesses`` chain names, in its own order."""
    try:
        with config.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read harness config {config}: {exc}") from exc
    chain = data.get("harnesses")
    if (
        not isinstance(chain, list)
        or not chain
        or not all(isinstance(item, str) and item for item in chain)
    ):
        raise ConfigError(
            f"{config} declares no 'harnesses' chain to select from; restore it from "
            "the repository, then retry"
        )
    return tuple(chain)


def resolve_harness_override(value: str, side: HarnessSide) -> str:
    """Validate one side's override and return the value oneharness will read.

    Raises `ConfigError` naming the rejected value and every selectable identity,
    so an operator sees which name to use instead of a run on the wrong provider.
    """
    selectable = configured_harnesses(side.config)
    requested = [item.strip() for item in value.split(",")]
    for item in requested:
        if item in selectable:
            continue
        raise ConfigError(
            f"{side.option} {value!r}: {item!r} is not a harness "
            f"{side.config.name} configures; select from "
            f"{', '.join(selectable)}"
        )
    return ",".join(requested)


def harness_override_env(*, worker: str | None = None, judge: str | None = None) -> dict[str, str]:
    """The environment carrying each side's explicit selection to the wrapper.

    Omitted sides contribute nothing, so a dispatch with neither override runs
    exactly as it did before: each side resolves its own config chain, and the
    agent branch still drops an alternate Claude identity this host never set up.
    """
    overrides = {}
    if worker is not None:
        overrides[WORKER_HARNESS_ENV] = resolve_harness_override(worker, WORKER_SIDE)
    if judge is not None:
        overrides[JUDGE_HARNESS_ENV] = resolve_harness_override(judge, JUDGE_SIDE)
    return overrides
