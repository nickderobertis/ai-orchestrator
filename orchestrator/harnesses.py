"""Per-side dispatch overrides: who does the work, who supervises, on what model.

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

**Model** is the same seam, one layer in. Lifting a judge off the cheaper
supervisor tier ``oneharness.judge.toml`` deliberately pins it to has no per-side
lever in oneharness: ``ONEHARNESS_MODEL`` is process-wide, so it would also reach
``oneharness.orchestrator.toml``'s codex-first chain and take the orchestrator
process down with a model that chain cannot run. Two more per-side variables carry
the choice instead, applied on the same two branches of the same wrapper.

What the wrapper does with them is *not* symmetric with the harness half, and the
difference is measured rather than assumed (oneharness 0.6.6). ``ONEHARNESS_MODEL``
lands in oneharness's ``environment`` **config** layer — a config's per-harness
``model`` outranks it, and every identity in this repository's four configs pins
one — so exporting it alone would move nothing here. Only ``--model`` on the
invocation's own argv beats a per-harness value, so the wrapper exports the
variable *and* passes the flag: the export is what everything that side then runs
inherits, and the flag is what moves the turn.

A model override is accepted only **paired with** that side's harness override,
naming identities of a single harness family. That pairing is a correctness
requirement rather than advice: ``--model`` applies to every candidate in the
chain, so an unpaired override would hand a Claude model name to a ``codex``
candidate the side's configured chain falls through to. Requiring the identity and
the model in one breath makes that unconstructable.

The model **value** is deliberately not checked against an allowlist, and the
asymmetry with the harness option is the point: a harness identity selects
credentials and environment routing that only this repository configures, so
naming an unconfigured one has to refuse, while a model name is passed through to
the harness the operator just named beside it and an unknown one fails loudly at
that provider rather than quietly running something else.
"""

from __future__ import annotations

import argparse
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
#: Every variable that decides which harness a process selects, named once.
HARNESS_SELECTION_ENV = (WORKER_HARNESS_ENV, JUDGE_HARNESS_ENV, PROCESS_WIDE_HARNESS_ENV)

#: The worker side's model, read by the wrapper's agent branch.
WORKER_MODEL_ENV = "ORCHESTRATOR_WORKER_MODEL"
#: The judge / simulated-user side's model, read by the wrapper's judge branch.
JUDGE_MODEL_ENV = "ORCHESTRATOR_JUDGE_MODEL"
#: oneharness's own process-wide model. The wrapper exports it, so everything that
#: side then runs inherits the choice — but a config's per-harness ``model`` outranks
#: it, which is why the wrapper also passes ``--model`` on that side's own run.
PROCESS_WIDE_MODEL_ENV = "ONEHARNESS_MODEL"
#: Every variable that decides which model a process runs on, named once.
MODEL_SELECTION_ENV = (WORKER_MODEL_ENV, JUDGE_MODEL_ENV, PROCESS_WIDE_MODEL_ENV)

#: Every variable an enclosing dispatch's choice can arrive under, named once. A
#: reader isolating itself from it — the test suite a worker's own gate runs — has
#: to drop all six: scrubbing only the per-side pairs leaves the two variables they
#: are resolved *into* in place, and those are the ones oneharness reads.
DISPATCH_SELECTION_ENV = HARNESS_SELECTION_ENV + MODEL_SELECTION_ENV


@dataclass(frozen=True)
class HarnessSide:
    """One conversation side: the flags that set it, the config and envs that carry it."""

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
    """The model half of that same one source, so the two cannot drift apart."""
    return (
        f"model the {side.name} side of the conversation runs on (via "
        f"{side.model_env}, applied to that side's own oneharness run and exported "
        f"as {PROCESS_WIDE_MODEL_ENV}), overriding the per-harness model in "
        f"{side.config.name}; requires {side.option} naming identities of one "
        f"harness family, and is not checked against an allowlist — an unknown name "
        f"fails at that harness"
    )


def add_side_options(parser: argparse.ArgumentParser, *, scope: str = "") -> None:
    """Offer both halves of both sides' overrides on one dispatching entry point.

    Every entry point takes the whole group from here, so the harness and model
    halves cannot come to differ between `just run-plan` and `just orchestrate`.
    ``scope`` is appended to each help string by a command that reaches more than
    one dispatch.
    """
    for side in (WORKER_SIDE, JUDGE_SIDE):
        parser.add_argument(
            side.option, default=None, metavar="ID", help=f"{harness_option_help(side)}{scope}"
        )
        parser.add_argument(
            side.model_option,
            default=None,
            metavar="MODEL",
            help=f"{model_option_help(side)}{scope}",
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
    """The provider a configured identity belongs to, apart from its variant."""
    return identity.split(":", 1)[0]


def resolve_model_override(value: str, side: HarnessSide, harness: str | None) -> str:
    """Validate one side's model against the harness override it must be paired with.

    Raises `ConfigError` naming the option and the reason, in the shape
    `resolve_harness_override` refuses by. The value itself is only checked for
    being a usable environment value — an unknown model is the named harness's to
    reject, loudly, which is why no allowlist appears here.
    """
    model = value.strip()
    if not model or any(character < " " or character == "\x7f" for character in model):
        raise ConfigError(
            f"{side.model_option} {value!r}: a model name must be a non-empty value "
            f"with no control characters; name the model {side.option}'s harness offers"
        )
    if harness is None:
        raise ConfigError(
            f"{side.model_option} {value!r}: name that side's harness too, with "
            f"{side.option}. A model is applied to every candidate that side runs, so "
            f"an unpaired one would be pushed onto whichever identity "
            f"{side.config.name}'s chain falls through to — a model name from the "
            f"wrong provider, which that provider rejects rather than degrades from"
        )
    selection = resolve_harness_override(harness, side)
    families = sorted({harness_family(item) for item in selection.split(",")})
    if len(families) > 1:
        raise ConfigError(
            f"{side.model_option} {value!r}: {side.option} {harness!r} spans "
            f"{' and '.join(families)}, and a model name belongs to one of them; "
            f"select a chain whose identities are all {families[0]}-based or all "
            f"{families[1]}-based"
        )
    return model


def side_override_env(
    *,
    worker: str | None = None,
    judge: str | None = None,
    worker_model: str | None = None,
    judge_model: str | None = None,
) -> dict[str, str]:
    """The environment carrying each side's explicit choices to the wrapper.

    Omitted sides contribute nothing, so a dispatch with no override runs exactly
    as it did before: each side resolves its own config chain and its own
    per-harness model, and the agent branch still drops an alternate Claude
    identity this host never set up.
    """
    overrides: dict[str, str] = {}
    for side, harness, model in (
        (WORKER_SIDE, worker, worker_model),
        (JUDGE_SIDE, judge, judge_model),
    ):
        if harness is not None:
            overrides[side.env] = resolve_harness_override(harness, side)
        if model is not None:
            overrides[side.model_env] = resolve_model_override(model, side, harness)
    return overrides
