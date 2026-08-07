"""Per-side selection: which provider does the work, which one supervises, on which model.

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

**Model is the same seam, one level in.** Each role's config pins a ``model`` per
harness — the judge's three Claude identities are pinned to the cheaper supervisor
tier by design — so choosing the identity does not choose the tier, and the only
process-wide lever, ``ONEHARNESS_MODEL``, is *not* the one it looks like: measured
against oneharness 0.6.6, a config's per-harness ``model`` beats it, while the
``--model`` flag on an invocation's own argv beats the config. So the wrapper does
both per branch — it names the model on that branch's own ``oneharness run`` and
exports the variable for everything the side subsequently runs. That precedence is a
fact about one release rather than about oneharness, so ``config/oneharness.version``
owns the literal above and ``tests/test_onejudge_version.py`` fails every restatement
of it — here, in the wrapper, and in the reference document — on an upgrade. The tier
and identity count this paragraph opens with are owned the same way by
``oneharness.judge.toml``, with ``tests/test_harness_routing.py`` holding every copy
of them to it.

A model override is accepted only **paired with that side's harness override**, and
only when the harness override names one harness family. A model name belongs to a
provider, one model applies to whichever candidate the chain selects, and
oneharness's ``fallback`` mode does not fall through a *task* failure — so an
unpaired model would reach a candidate of another provider and kill the dispatch on
a rejection instead of degrading. Requiring the identity and the model to be named
together makes that mismatch unconstructable. The model **value** is deliberately
not checked against an allowlist, unlike the identity: an identity selects
credentials and environment routing only this repository configures, while a model
name is passed straight through to the harness the operator just named, where an
unknown one fails loudly rather than quietly running something else.
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
#: oneharness's own process-wide model. The wrapper *exports* it alongside naming the
#: model on the branch's own invocation, so everything that side then runs — its gate,
#: and the llmlint tier inside that gate — carries the same choice.
PROCESS_WIDE_MODEL_ENV = "ONEHARNESS_MODEL"
#: The model counterpart of `HARNESS_SELECTION_ENV`, for the same reason and read the
#: same way: all three names one value can arrive under.
MODEL_SELECTION_ENV = (WORKER_MODEL_ENV, JUDGE_MODEL_ENV, PROCESS_WIDE_MODEL_ENV)
#: The one place a reader isolating itself from an enclosing dispatch drops *every*
#: choice that dispatch made for a side. Iterating `HARNESS_SELECTION_ENV` alone would
#: leave the model behind, which is a different question answered by the same leak.
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
    """The model half of the same option group, from the same one source.

    Generated beside `harness_option_help` so the two halves cannot drift apart:
    an operator reading `--help` learns the pairing rule from the option that
    enforces it, rather than from a refusal after the fact.
    """
    return (
        f"model the {side.name} side of the conversation runs on "
        f"(via {side.model_env}), overriding the model {side.config.name} pins for "
        f"the selected identity; requires {side.option} naming identities of a "
        "single harness family, and is passed to that harness unchecked"
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
    """The harness a composed identity belongs to — `claude-code:alternate2` is one."""
    return identity.split(":", 1)[0]


def resolve_model_override(value: str, side: HarnessSide, *, harness: str | None) -> str:
    """Validate one side's model against that side's identity, and return it verbatim.

    Raises `ConfigError` naming the option and the reason, in the shape
    `resolve_harness_override` refuses by. What is checked is the *pairing*, never
    the model name: see this module's docstring for why those are opposite calls.
    """
    model = value.strip()
    if not model:
        raise ConfigError(
            f"{side.model_option} {value!r}: name the model to run the {side.name} side on, "
            f"or omit {side.model_option} to use the one {side.config.name} pins"
        )
    if harness is None:
        raise ConfigError(
            f"{side.model_option} {model!r}: requires {side.option}. A model name belongs to "
            "one provider, and oneharness's fallback chain does not fall through a task "
            f"failure — so an unpaired model reaches whichever candidate {side.config.name}'s "
            "chain selects and kills the dispatch on a provider rejection. Name the identity "
            "and the model together."
        )
    selected = resolve_harness_override(harness, side).split(",")
    families = sorted({harness_family(identity) for identity in selected})
    if len(families) > 1:
        raise ConfigError(
            f"{side.model_option} {model!r}: {side.option} {harness!r} spans "
            f"{', '.join(families)}, and one model cannot name a model of each. Narrow "
            f"{side.option} to identities of a single harness."
        )
    return model


def side_override_env(
    *,
    worker: str | None = None,
    judge: str | None = None,
    worker_model: str | None = None,
    judge_model: str | None = None,
) -> dict[str, str]:
    """The environment carrying each side's explicit selection to the wrapper.

    Omitted sides contribute nothing, so a dispatch with no override runs exactly
    as it did before: each side resolves its own config chain and its own pinned
    model, and the agent branch still drops an alternate Claude identity this host
    never set up.

    A model is validated against the identity it is paired with, so both halves of
    one side's choice are refused here — before anything is dispatched — rather
    than one at the boundary and one at a provider that rejects the model name.
    """
    overrides = {}
    if worker is not None:
        overrides[WORKER_HARNESS_ENV] = resolve_harness_override(worker, WORKER_SIDE)
    if judge is not None:
        overrides[JUDGE_HARNESS_ENV] = resolve_harness_override(judge, JUDGE_SIDE)
    if worker_model is not None:
        overrides[WORKER_MODEL_ENV] = resolve_model_override(
            worker_model, WORKER_SIDE, harness=worker
        )
    if judge_model is not None:
        overrides[JUDGE_MODEL_ENV] = resolve_model_override(judge_model, JUDGE_SIDE, harness=judge)
    return overrides
