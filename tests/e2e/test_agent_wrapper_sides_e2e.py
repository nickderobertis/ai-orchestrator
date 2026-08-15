"""Which conversation side `scripts/oneharness-agent.sh` thinks it is, driven for real.

onejudge routes both sides of a run through that one wrapper, and everything the agent
side gets — its own harness chain, its own model, the absent-alternate substitution, the
streaming branch — hangs off the wrapper working out which side it is. It used to work
that out from the *absence* of `--config`, because the agent side's config was implicit.

onepipeline 0.3.1 names both sides' configs, so that rule read every agent turn as a
judge turn. `just smoke` and the manual probes in docs/host-setup.md are what run
through this wrapper, and under the old rule they silently stopped applying the agent
side's selection: the turn still ran, with the judge's routing and the config's own
model. Nothing failed loudly, which is why this is held here rather than left to a
launch to notice.

The rule now is the name confirmed by the config's own declared `role`: the name selects
the side, that role must be exactly the role of the side it selected, and a turn carrying
no config is the agent side running from this repository's own agent config. A role that
disagrees, is unrecognized, or is absent stops the turn rather than resolving to either
side — so no config a caller names can route a side without declaring it is that side.
These journeys drive the real
wrapper with the real `oneharness` CLI and read the command it resolved, so what is
asserted is the routing a real turn would run under. `--print-command` is what makes
that free: oneharness resolves configs, identity, and model exactly as it would, then
prints the invocation instead of spending it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The wrapper under test, and the two config basenames `oneagentgraph` writes into a
#: member's scratch. Both names are the graph's, not this test's: give a graph
#: `worker.toml` and `judge.toml` through `--set` and it still writes these two.
WRAPPER = REPO_ROOT / "scripts" / "oneharness-agent.sh"
AGENT_CONFIG_NAME = "oneharness.toml"
JUDGE_CONFIG_NAME = "oneharness.judge.toml"

#: A model neither config pins, so seeing it proves the wrapper applied that side's
#: `--model` rather than letting the config's own value stand. One per side, because
#: the point of the per-side variables is that neither reaches the other's turn.
WORKER_MODEL = "worker-model-sentinel"
JUDGE_MODEL = "judge-model-sentinel"

#: An identity both shipped configs configure, so naming it exercises the selection
#: path without narrowing either chain to something it does not declare.
SHARED_IDENTITY = "codex"


@pytest.fixture
def member_scratch(tmp_path: Path) -> Path:
    """A member directory shaped the way `oneagentgraph` writes one for a two-party member.

    Both configs, under the basenames the graph normalizes to. They are this
    repository's real ones rather than fixtures: the wrapper validates a requested
    identity against the config the turn will run from, so a stand-in config would
    make that refusal a statement about the stand-in.
    """
    member = tmp_path / "members" / "worker"
    member.mkdir(parents=True)
    shutil.copy(REPO_ROOT / AGENT_CONFIG_NAME, member / AGENT_CONFIG_NAME)
    shutil.copy(REPO_ROOT / JUDGE_CONFIG_NAME, member / JUDGE_CONFIG_NAME)
    return member


def _wrapper(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the real wrapper for one turn, with no status directory watching it.

    Without `ORCHESTRATOR_AGENT_STATUS_DIR` the agent side takes its `--events` branch
    and `exec`s directly, which is the shape `just smoke` and a manual probe both use.
    """
    return subprocess.run(
        ["bash", str(WRAPPER), "run", *arguments],
        cwd=REPO_ROOT,
        env={
            **_base_environment(),
            **environment,
        },
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _base_environment() -> dict[str, str]:
    """The ambient values the wrapper needs, and nothing that would pick a side for it."""
    inherited = dict(os.environ)
    for named in (
        "ORCHESTRATOR_WORKER_HARNESSES",
        "ORCHESTRATOR_JUDGE_HARNESSES",
        "ORCHESTRATOR_WORKER_MODEL",
        "ORCHESTRATOR_JUDGE_MODEL",
        "ORCHESTRATOR_AGENT_STATUS_DIR",
        "ONEHARNESS_HARNESSES",
        "ONEHARNESS_MODEL",
    ):
        inherited.pop(named, None)
    # The wrapper puts the project venv ahead of everything, but it resolves plain
    # `oneharness` from PATH; this is the checkout's own, at the adopted release.
    inherited["PATH"] = f"{REPO_ROOT / '.venv' / 'bin'}:{inherited['PATH']}"
    return inherited


class PlannedCandidate(TypedDict):
    """The two fields of a `--print-command` candidate these journeys are about.

    Routing, and only routing: which identity the turn would reach and at which model.
    oneharness returns two dozen more fields per candidate, and naming them here would
    be this test restating that report's schema — a second copy of somebody else's
    contract, which is the thing that goes stale. What is declared is what is read.
    """

    harness_id: str
    model: str


class PrintedCommand(TypedDict):
    """What `oneharness run --print-command` answers: the candidate chain it resolved."""

    results: list[PlannedCandidate]


def _planned(completed: subprocess.CompletedProcess[str]) -> list[PlannedCandidate]:
    """Every candidate oneharness resolved for this turn, from `--print-command`.

    A chain, not a single entry: a side that named one identity narrows it to that one,
    and a side that named none plans the whole configured chain in order. Both are
    answers about routing, so both are returned rather than one being assumed.
    """
    assert completed.returncode == 0, completed.stderr
    # `cast` rather than a validating read: the shape is oneharness's own report, this
    # is the adopted release printing it, and the assertions below fail loudly on any
    # field that is missing or not what it claims.
    printed = cast(PrintedCommand, json.loads(completed.stdout))
    candidates = printed["results"]
    assert candidates, f"oneharness planned no candidate at all: {printed}"
    return candidates


def _resolved(completed: subprocess.CompletedProcess[str]) -> PlannedCandidate:
    """The single candidate a side that narrowed its own selection resolved to."""
    candidates = _planned(completed)
    assert len(candidates) == 1, f"expected one resolved candidate, got {candidates}"
    return candidates[0]


def test_the_agent_side_is_identified_when_the_graph_names_its_config(
    member_scratch: Path,
) -> None:
    """A turn carrying the agent config is the agent side, and is routed as one.

    This is the shape onepipeline 0.3.1 introduced and the one the old absence rule got
    wrong. What proves the branch ran is the worker model reaching the resolved
    invocation: it is pinned by neither config, so it can only be there because the
    wrapper applied `ORCHESTRATOR_WORKER_MODEL`, which only the agent branch reads.
    """
    completed = _wrapper(
        "--config",
        str(member_scratch / AGENT_CONFIG_NAME),
        "--print-command",
        "--prompt",
        "answer this turn",
        environment={
            "ORCHESTRATOR_WORKER_MODEL": WORKER_MODEL,
            "ORCHESTRATOR_WORKER_HARNESSES": SHARED_IDENTITY,
        },
    )

    resolved = _resolved(completed)
    assert resolved["model"] == WORKER_MODEL, (
        "the agent side's model did not reach the turn, so the wrapper did not take "
        f"the agent branch for a config-carrying agent turn: {resolved}"
    )
    assert resolved["harness_id"] == SHARED_IDENTITY, resolved


def test_the_agent_side_reads_the_config_the_caller_named_and_not_the_repositorys(
    member_scratch: Path,
) -> None:
    """A member whose config was overridden is judged against that config, not this checkout's.

    `oneharness run` refuses a repeated `--config`, so an agent branch that injected the
    repository's copy on top of the caller's could not start the turn at all — and one
    that validated against the repository's copy while running the caller's would admit
    an identity the turn's own config never declared.

    Narrowing the caller's copy to one identity is what separates the two files: the
    refusal has to be about the narrowed chain, and it has to quote the narrowed path.
    `claude-code:primary` is declared by this repository's `oneharness.toml` and by the
    copy until it is narrowed, so it can only be refused by reading the narrowed one.
    """
    narrowed = member_scratch / AGENT_CONFIG_NAME
    original = narrowed.read_text(encoding="utf-8")
    chain_opens = original.index("harnesses = [")
    chain_closes = original.index("]", chain_opens) + 1
    narrowed.write_text(
        original[:chain_opens] + f'harnesses = ["{SHARED_IDENTITY}"]' + original[chain_closes:],
        encoding="utf-8",
    )
    assert "claude-code:primary" in original, (
        "this journey turns on the repository's own agent chain declaring "
        "claude-code:primary; it no longer does, so pick another identity it declares"
    )

    refused = _wrapper(
        "--config",
        str(narrowed),
        "--print-command",
        "--prompt",
        "answer this turn",
        environment={"ORCHESTRATOR_WORKER_HARNESSES": "claude-code:primary"},
    )

    assert refused.returncode == 2, (
        "an identity the caller's own config does not declare was accepted, so the "
        f"selection was judged against some other file:\n{refused.stdout}"
    )
    assert str(narrowed) in refused.stderr, refused.stderr
    assert f"select from {SHARED_IDENTITY}" in refused.stderr, (
        f"the refusal must offer the narrowed chain the caller's config declares: {refused.stderr}"
    )


def test_the_judge_side_is_still_identified_by_its_own_config(member_scratch: Path) -> None:
    """The side that was already explicit stays explicit, and keeps its own routing."""
    completed = _wrapper(
        "--config",
        str(member_scratch / JUDGE_CONFIG_NAME),
        "--print-command",
        "--prompt",
        "supervise this turn",
        environment={
            "ORCHESTRATOR_JUDGE_MODEL": JUDGE_MODEL,
            "ORCHESTRATOR_JUDGE_HARNESSES": SHARED_IDENTITY,
        },
    )

    resolved = _resolved(completed)
    assert resolved["model"] == JUDGE_MODEL, (
        f"the judge side's model did not reach the turn it was set for: {resolved}"
    )
    assert resolved["harness_id"] == SHARED_IDENTITY, resolved


@pytest.mark.parametrize(
    ("config_name", "prompt", "foreign_model", "foreign_harnesses", "foreign_model_value"),
    [
        (
            JUDGE_CONFIG_NAME,
            "supervise this turn",
            "ORCHESTRATOR_WORKER_MODEL",
            "ORCHESTRATOR_WORKER_HARNESSES",
            WORKER_MODEL,
        ),
        (
            AGENT_CONFIG_NAME,
            "answer this turn",
            "ORCHESTRATOR_JUDGE_MODEL",
            "ORCHESTRATOR_JUDGE_HARNESSES",
            JUDGE_MODEL,
        ),
    ],
    ids=["worker-values-on-the-judge-turn", "judge-values-on-the-agent-turn"],
)
def test_neither_sides_selection_reaches_the_other(
    member_scratch: Path,
    config_name: str,
    prompt: str,
    foreign_model: str,
    foreign_harnesses: str,
    foreign_model_value: str,
) -> None:
    """A side reads its own variables and never the other's, in both directions.

    Under the absence rule the two turns were told apart by a property the agent turn no
    longer has, so an agent turn would have read the judge's values. Both directions are
    driven because the branches read different variables and one could leak while the
    other does not — and because only setting the foreign pair proves the leak rather
    than a coincidence: the side's own config model has to stand, and it can only do that
    if nothing on that branch read the variable that was set.
    """
    completed = _wrapper(
        "--config",
        str(member_scratch / config_name),
        "--print-command",
        "--prompt",
        prompt,
        environment={foreign_model: foreign_model_value, foreign_harnesses: SHARED_IDENTITY},
    )

    # Nothing this side reads was set, so its config's whole chain is planned and not one
    # candidate may carry the other side's model.
    carrying = [
        candidate for candidate in _planned(completed) if candidate["model"] == foreign_model_value
    ]
    assert not carrying, f"{foreign_model} reached a turn that must not read it: {carrying}"


def test_a_turn_with_no_config_is_still_the_agent_side(tmp_path: Path) -> None:
    """`just smoke` and the manual probes name no config, and must keep getting the agent's.

    This is the case the forced agent config exists for and the only one where target
    project discovery could otherwise win, so it survives the rule change unchanged.
    """
    completed = _wrapper(
        "--print-command",
        "--prompt",
        "answer this turn",
        environment={
            "ORCHESTRATOR_WORKER_MODEL": WORKER_MODEL,
            "ORCHESTRATOR_WORKER_HARNESSES": SHARED_IDENTITY,
        },
    )

    resolved = _resolved(completed)
    assert resolved["model"] == WORKER_MODEL, resolved


def test_an_unusable_worker_selection_is_refused_and_recovers_when_corrected(
    member_scratch: Path,
) -> None:
    """The failure path names the config it judged against, and naming a real identity clears it.

    This is the agent branch's own validation, and it is now checked against the config
    the caller named rather than the repository's copy — so a member whose config was
    overridden gets a refusal about the file its turn would really run from. A wrapper
    that had taken the judge branch here would never reach this check at all, and the
    turn would have run with an unasked-for identity instead of stopping.
    """
    agent_config = member_scratch / AGENT_CONFIG_NAME
    refused = _wrapper(
        "--config",
        str(agent_config),
        "--print-command",
        "--prompt",
        "answer this turn",
        environment={"ORCHESTRATOR_WORKER_HARNESSES": "not-a-configured-identity"},
    )

    assert refused.returncode == 2, refused.stdout
    assert "not-a-configured-identity" in refused.stderr, refused.stderr
    assert str(agent_config) in refused.stderr, (
        "the refusal must name the config it judged the selection against, which is the "
        f"one the caller passed: {refused.stderr}"
    )

    recovered = _wrapper(
        "--config",
        str(agent_config),
        "--print-command",
        "--prompt",
        "answer this turn",
        environment={"ORCHESTRATOR_WORKER_HARNESSES": SHARED_IDENTITY},
    )

    assert _resolved(recovered)["harness_id"] == SHARED_IDENTITY, recovered.stdout


class Misdeclared(NamedTuple):
    """One config whose declared role is not the one its name selects.

    ``role`` is the value to write into a fresh config, or ``None`` to copy one of this
    repository's real configs under a name that selects the other side — which is the
    shape a rename produces and the one a fixture could not stand in for.
    """

    #: What the config is named, which is what selects the side it would run as.
    name: str
    #: The role to declare, `""` to declare none, or `None` to copy a real config.
    role: str | None
    #: The role the refusal must report having found.
    reported: str


#: Every shape whose declared role is not its side's. Together they leave nothing that
#: routes a side without declaring it: the judge's file under another name, an agent
#: file under the judge's name, a role that is neither, and no role at all.
MISDECLARED = {
    "the-judges-config-under-another-name": Misdeclared("sneaky.toml", None, "judge"),
    "an-agent-config-under-the-judges-name": Misdeclared(JUDGE_CONFIG_NAME, None, "agent"),
    "a-role-that-is-neither-side": Misdeclared(AGENT_CONFIG_NAME, "whatever", "whatever"),
    "no-declared-role-at-all": Misdeclared(AGENT_CONFIG_NAME, "", "none"),
}


@pytest.mark.parametrize("case", list(MISDECLARED.values()), ids=list(MISDECLARED))
def test_a_config_whose_declared_role_is_not_its_sides_is_refused(
    member_scratch: Path, tmp_path: Path, case: Misdeclared
) -> None:
    """A side is never entered on a name a caller chose without the config declaring it.

    The name selects the branch, so on its own it would let any readable file a caller
    named `oneharness.judge.toml` take the judge's routing — and, the other way, let a
    judge config renamed to anything else run as the agent side. Every config that
    reaches this wrapper says which it is in its own `history_labels` `role`, so the
    name and that role must agree exactly. An absent or unrecognized role is refused
    too, rather than defaulting to a side.

    A disagreement stops the turn instead of resolving to either side, because a turn
    routed as the side it is not still runs and still answers — which is the failure
    this whole rule exists to prevent.
    """
    written = tmp_path / "candidate"
    written.mkdir()
    candidate = written / case.name
    if case.role is None:
        source = AGENT_CONFIG_NAME if case.name == JUDGE_CONFIG_NAME else JUDGE_CONFIG_NAME
        candidate.write_bytes((member_scratch / source).read_bytes())
    else:
        labels = f"history_labels = {{ role = {case.role!r} }}\n" if case.role else ""
        candidate.write_text(f'harnesses = ["{SHARED_IDENTITY}"]\n{labels}', encoding="utf-8")

    refused = _wrapper(
        "--config", str(candidate), "--print-command", "--prompt", "take this turn", environment={}
    )

    assert refused.returncode == 2, refused.stdout
    assert str(candidate) in refused.stderr, refused.stderr
    assert f"role '{case.reported}'" in refused.stderr, refused.stderr


def test_a_config_the_caller_named_but_did_not_write_is_refused(member_scratch: Path) -> None:
    """An absent config is refused before any turn, on the side that named it.

    A member scratch is written per run and can be swept while a turn is being retried,
    so this is a real state rather than a typo. The refusal names the path, which is the
    whole recovery: the caller either restores it or relaunches.
    """
    refused = _wrapper(
        "--config",
        str(member_scratch / "never-written.toml"),
        "--print-command",
        "--prompt",
        "answer this turn",
        environment={},
    )

    assert refused.returncode == 2, refused.stdout
    assert "never-written.toml" in refused.stderr, refused.stderr


#: What a stand-in checkout's `oneharness.toml` declares, and what the wrapper must do
#: with a turn that names no config and therefore runs from it. `None` is a file with no
#: `history_labels` table at all, which is how the field goes missing in practice —
#: someone drops the block rather than blanking the value.
IMPLICIT_ROLES = {
    "the-judges-role": ("judge", "judge"),
    "a-role-that-is-neither-side": ("llmlint", "llmlint"),
    "no-declared-role-at-all": (None, "none"),
}


@pytest.fixture
def standin_checkout(tmp_path: Path) -> Path:
    """A checkout laid out the way the wrapper resolves its own root, with no venv.

    `repo_root` is the parent of the directory the wrapper is invoked from, and the
    implicit agent config is read from there — so the only way to drive a turn against a
    *different* implicit config is to give the wrapper a different root. The whole
    `scripts/` directory is copied because the wrapper sources two helpers out of it and
    refuses the turn when either is missing, which would be a refusal about the stand-in
    rather than about the role under test.
    """
    scripts = tmp_path / "scripts"
    shutil.copytree(REPO_ROOT / "scripts", scripts)
    return tmp_path


def _standin_wrapper(
    checkout: Path, *arguments: str, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run the wrapper as the stand-in checkout's own, so it reads that root's config."""
    return subprocess.run(
        ["bash", str(checkout / "scripts" / "oneharness-agent.sh"), "run", *arguments],
        cwd=checkout,
        env={**_base_environment(), **environment},
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


@pytest.mark.parametrize(
    ("declared", "reported"), list(IMPLICIT_ROLES.values()), ids=list(IMPLICIT_ROLES)
)
def test_the_implicit_agent_config_must_declare_the_agent_role_too(
    standin_checkout: Path, declared: str | None, reported: str
) -> None:
    """The config nobody named is held to the same rule as the one a caller does.

    A caller's config has to declare the side its name selects; the implicit one used to
    be exempt, so an `oneharness.toml` whose role stopped saying `agent` still ran every
    turn that named no config — `just smoke`, the manual probes, and any dispatch whose
    agent side arrives without one. Nothing failed: the turn ran and answered, and only
    the history store recorded it under another side's label, which is precisely the
    quiet wrong-side outcome the declared-role rule exists to stop.
    """
    labels = "" if declared is None else f"history_labels = {{ role = {declared!r} }}\n"
    (standin_checkout / AGENT_CONFIG_NAME).write_text(
        f'harnesses = ["{SHARED_IDENTITY}"]\n{labels}', encoding="utf-8"
    )

    refused = _standin_wrapper(
        standin_checkout, "--print-command", "--prompt", "take this turn", environment={}
    )

    assert refused.returncode == 2, refused.stdout
    assert str(standin_checkout / AGENT_CONFIG_NAME) in refused.stderr, refused.stderr
    assert f"role '{reported}'" in refused.stderr, refused.stderr


def test_an_implicit_agent_config_declaring_the_agent_role_still_takes_its_turn(
    standin_checkout: Path,
) -> None:
    """The control for the refusals above: the same stand-in runs when the role agrees.

    Without it those three assertions would pass just as well against a stand-in the
    wrapper could not use at all, and the new check would be indistinguishable from a
    broken fixture. Here the only thing that changes is the declared role, and the turn
    resolves — so the refusal is caused by the role and by nothing else.
    """
    (standin_checkout / AGENT_CONFIG_NAME).write_text(
        f'harnesses = ["{SHARED_IDENTITY}"]\nhistory_labels = {{ role = "agent" }}\n',
        encoding="utf-8",
    )

    completed = _standin_wrapper(
        standin_checkout,
        "--print-command",
        "--prompt",
        "take this turn",
        environment={
            "ORCHESTRATOR_WORKER_MODEL": WORKER_MODEL,
            "ORCHESTRATOR_WORKER_HARNESSES": SHARED_IDENTITY,
        },
    )

    resolved = _resolved(completed)
    assert resolved["model"] == WORKER_MODEL, resolved
    assert resolved["harness_id"] == SHARED_IDENTITY, resolved
