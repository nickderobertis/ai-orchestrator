"""Per-side selection: which provider does the work, which supervises, at what tier.

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

**Which model each side runs on** is the same seam one layer down, and it exists
because a config pins one per identity: ``oneharness.judge.toml`` names
``claude-sonnet-5`` on every Claude variant by design, so putting the judge on the
primary subscription still supervises at the cheaper tier. ``ORCHESTRATOR_WORKER_MODEL``
and ``ORCHESTRATOR_JUDGE_MODEL`` carry that choice per side exactly as the pair above
carries the identity, and the wrapper applies each to only its own branch — twice
over, because oneharness's two model layers are not equally strong: exported as
``ONEHARNESS_MODEL``, which everything that side then runs inherits, and passed as
``--model`` on that branch's own turn, which is the layer that beats the per-harness
value a config pins.

A model is accepted only **together with that side's harness override**, naming
identities of a single harness family. The reason is that a model name belongs to
the provider it is written for: an unpaired override would put it in front of every
candidate in that side's configured chain, including one from another provider, and
``fallback`` moves past a candidate that cannot run rather than one whose task
failed — so the dispatch would die on a provider rejection instead of degrading.
Requiring the identity and the model in the same breath makes that mismatch
unconstructable. The model *value* is deliberately not checked against a list:
an identity selects credentials and environment routing only this repository
configures, while a model name is passed to the harness the operator just named
and an unknown one fails loudly at that provider.
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
#: oneharness's own process-wide selection — the one the two above exist to displace.
#: The wrapper *exports* it, so it is how a per-side choice actually reaches oneharness
#: and how it leaves a dispatch: everything that dispatch runs, its own gate included,
#: inherits it.
PROCESS_WIDE_HARNESS_ENV = "ONEHARNESS_HARNESSES"
#: Every variable that decides which harness a process selects, named once. A reader
#: isolating itself from an enclosing dispatch's choice — the test suite a worker's own
#: gate runs — has to drop all three: scrubbing only the per-side pair leaves the
#: variable they are resolved *into* in place, which is the value oneharness reads.
HARNESS_SELECTION_ENV = (WORKER_HARNESS_ENV, JUDGE_HARNESS_ENV, PROCESS_WIDE_HARNESS_ENV)

#: The worker side's model, read by the wrapper's agent branch.
WORKER_MODEL_ENV = "ORCHESTRATOR_WORKER_MODEL"
#: The judge / simulated-user side's model, read by the wrapper's judge branch.
JUDGE_MODEL_ENV = "ORCHESTRATOR_JUDGE_MODEL"
#: oneharness's own process-wide model. The wrapper exports it, so this is how a
#: per-side model leaves a dispatch: everything that side then runs inherits it.
PROCESS_WIDE_MODEL_ENV = "ONEHARNESS_MODEL"
#: The model counterpart of `HARNESS_SELECTION_ENV`, with the same membership rule.
MODEL_SELECTION_ENV = (WORKER_MODEL_ENV, JUDGE_MODEL_ENV, PROCESS_WIDE_MODEL_ENV)
#: Every variable an enclosing dispatch's choice can arrive under, named once — the
#: one list a reader drops to state its own. Isolating against the harness names
#: alone would leave an ambient model in place, which is the same failure one layer
#: down: a suite that believes it is isolated while a choice nobody made reaches it.
DISPATCH_SELECTION_ENV = HARNESS_SELECTION_ENV + MODEL_SELECTION_ENV


@dataclass(frozen=True)
class HarnessSide:
    """One conversation side: the flags that set it, the config and env that carry them."""

    name: str
    option: str
    env: str
    config: Path
    model_option: str
    model_env: str


WORKER_SIDE = HarnessSide(
    name="worker",
    option="--worker-harness",
    env=WORKER_HARNESS_ENV,
    config=REPO_ROOT / "oneharness.toml",
    model_option="--worker-model",
    model_env=WORKER_MODEL_ENV,
)
JUDGE_SIDE = HarnessSide(
    name="judge",
    option="--judge-harness",
    env=JUDGE_HARNESS_ENV,
    config=REPO_ROOT / "oneharness.judge.toml",
    model_option="--judge-model",
    model_env=JUDGE_MODEL_ENV,
)


def harness_option_help(side: HarnessSide) -> str:
    """One source for the option help every dispatching entry point offers."""
    return (
        f"harness identity running the {side.name} side of the conversation "
        f"(via {side.env}), comma-separated for a fallback chain of its own; "
        f"selected from {side.config.name}'s chain, which is also the default"
    )


def model_option_help(side: HarnessSide) -> str:
    """One source for the model option's help, so the pair cannot drift apart."""
    return (
        f"model the {side.name} side of the conversation runs on (via "
        f"{side.model_env}), overriding the model {side.config.name} pins for the "
        f"identity; requires {side.option}, naming identities of one harness family"
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


def harness_family(identity: str) -> str:
    """The provider an identity belongs to: everything before its variant suffix."""
    return identity.split(":", 1)[0]


def resolve_model_override(value: str, side: HarnessSide, *, harness: str | None) -> str:
    """Validate one side's model against the identity it was named with.

    ``harness`` is that side's already-resolved chain, or None when the side named
    no identity — which is itself the first refusal, because a model belongs to the
    provider it was written for.
    """
    model = value.strip()
    if not model:
        raise ConfigError(
            f"{side.model_option} {value!r}: name the model the {side.name} side "
            f"should run on, or omit the option"
        )
    if harness is None:
        raise ConfigError(
            f"{side.model_option} {model!r}: also pass {side.option}, because this "
            f"model would otherwise reach every candidate in {side.config.name}'s "
            "chain — including one from another provider, which a fallback chain "
            "does not move past once its task fails"
        )
    families = sorted({harness_family(identity) for identity in harness.split(",")})
    if len(families) > 1:
        raise ConfigError(
            f"{side.model_option} {model!r}: {side.option} {harness!r} spans "
            f"{' and '.join(families)}, and one model name cannot be right for "
            "both; narrow that chain to one harness family, or drop this option"
        )
    return model


def harness_override_env(
    *,
    worker: str | None = None,
    judge: str | None = None,
    worker_model: str | None = None,
    judge_model: str | None = None,
) -> dict[str, str]:
    """The environment carrying each side's explicit identity and model to the wrapper.

    Omitted sides contribute nothing, so a dispatch with no override runs exactly as
    it did before: each side resolves its own config chain and the model that chain's
    identity pins, and the agent branch still drops an alternate Claude identity this
    host never set up. A model is validated against the identity it was named with,
    so the two halves of one side are always decided together.
    """
    overrides: dict[str, str] = {}
    for side, harness, model in (
        (WORKER_SIDE, worker, worker_model),
        (JUDGE_SIDE, judge, judge_model),
    ):
        resolved = None if harness is None else resolve_harness_override(harness, side)
        if resolved is not None:
            overrides[side.env] = resolved
        if model is not None:
            overrides[side.model_env] = resolve_model_override(model, side, harness=resolved)
    return overrides
