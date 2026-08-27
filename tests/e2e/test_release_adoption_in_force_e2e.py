"""Prove, against the installed artifacts, what release adoption does and does not do here.

`AGENTS.md`'s "Sequencing a node behind a release" used to describe a mechanism no
release this host installed contained. All three of its halves are adopted now, and
the section makes a narrower and more dangerous pair of claims in their place: that
this host **can** perform them, and that nothing here **does**. Both are invisible from
a run — a host that declares no release target executes a plan identically whether the
mechanism is present or absent — so both are driven here rather than asserted.

Five things are measured, against the copies that would have to carry them and the
loader that reads a plan:

* the `onevcs` CLI the manager verbs run, which is `config/onevcs.version` and which
  has to carry the `release` verb group for a target to be configurable at all;
* that the same CLI reports **no release targets** for this repository, which is what
  makes "nothing here uses it" a measurement rather than an assumption;
* the `onevcs` the adopted engine **links**, which is what a dispatch publishes
  through and what an engine resolving a node's adoption mode calls — read out of the
  engine binary itself, because that is the artifact that runs;
* the plan loader, which has to read `adoption` and `consumes` and refuse a bad value
  of each before anything is dispatched;
* the read API `just telemetry-server` runs, which is the third half's own artifact
  and the one a claim about the view rests on.

The view half is measured further in `tests/e2e/test_dag_ui_serving_e2e.py`, because
what a view does is what it serves: that journey starts both recipes on one origin
and reads the release surface out of the bundle a browser would have got.

The day the pins move under any of it, this journey fails and the section comes due.

Marked `reads_checkouts` for the reason that marker exists: its subject is an
installed producer rather than anything in this workspace, so no cache key here
describes it and a memoized verdict would replay a green straight across the upgrade
this exists to catch.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from project_fixtures import local_project
from registered_checkouts import registered_checkouts
from test_linked_engine_reconciliation_e2e import LINKED_IN_BINARY
from test_linked_libraries import Release
from test_release_adoption_guidance import (
    GUIDANCE_SECTION,
    IN_FORCE_UNUSED,
    RELEASE_MODES_FLOOR,
    RELEASE_SURFACE_FLOOR,
    RELEASE_VIEW_FLOOR,
    flat,
    section,
)
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The verb group release adoption exists to add, and the four subcommands under it.
#: All four, because the section describes what each one is for and a build carrying
#: three of them would leave one of those descriptions about nothing.
RELEASE_VERB = "release"
RELEASE_SUBCOMMANDS = ("targets", "latest", "status", "acknowledge")

#: How the recipes reach the CLI whose version `config/onevcs.version` pins — the same
#: `uv run` every manager verb in the justfile is a wrapper over, so what this journey
#: measures is the copy a manager would actually get.
ONEVCS = ("uv", "run", "onevcs")

#: The engine binary a dispatch runs, installed by `scripts/python-install.sh` into
#: this checkout's own environment.
ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"
#: How the recipes reach the read API, which is the view half's own artifact: the same
#: `uv run` `scripts/telemetry-server.sh` ends in, so this measures the copy an
#: operator would serve rather than whatever else is on PATH.
READ_API = ("uv", "run", "onepipeline-api")

#: This repository, as `onevcs` resolves it. Asked about itself rather than about a
#: sibling because it is the one identity this checkout is guaranteed to be inside.
THIS_REPOSITORY = "ai-orchestrator"

#: The mode a repository with nothing declared resolves to, which is the whole reason
#: a plan naming neither field runs as it always did.
DEFAULT_ADOPTION = "fast"

#: How `onevcs` refuses a repository its registry does not hold. `config/onevcs.checkouts`
#: is what this host *will* register and the registry is what it *has*, and the two part
#: company from the moment a registration lands in `config/` until `just repos-apply`
#: installs it — so a listed identity can be one no `onevcs` verb can resolve at all.
NOT_REGISTERED = "is not a registered repository"


def _onevcs(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*ONEVCS, *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _registry_identities() -> frozenset[str]:
    """Every identity this host's `onevcs` registry actually holds.

    `onevcs repos` prints one unindented `identity<TAB>…` line per repository above
    its indented checkouts, and that listing — rather than the tracked list — is what
    "registered here" means to every other `onevcs` verb.
    """
    listed = _onevcs("repos")
    assert listed.returncode == 0, listed.stderr
    return frozenset(
        line.split("\t", 1)[0]
        for line in listed.stdout.splitlines()
        if line and not line.startswith(" ")
    )


def _plan(tmp_path: Path, name: str, tasks: list[dict[str, object]]) -> str:
    """A local project the engine's own loader will read, and nothing will dispatch.

    Every case below is a **load-time** refusal, so the loader is reached and the
    scheduler is not. That is what makes driving the real `onepipeline start` safe
    here: a plan it refuses spends no dispatch, launches no agent, and writes no run.
    """
    return local_project(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "prove the loader reads release adoption's two fields"},
                "name": name,
                "tasks": tasks,
            }
        ),
        f"{name}-{tmp_path.name}",
    )


def _task(node: str, **extra: object) -> dict[str, object]:
    return {
        "id": node,
        "persona": "engineer",
        "task": "## What\nx\n\n## Why\ny\n\n## Acceptance criteria\n- z\n",
        **extra,
    }


def _refused(plan: str) -> str:
    """What the engine said refusing `plan`, having proved it refused it.

    **Every plan handed to this helper must be one the loader rejects**, and that is a
    safety property rather than a style: `onepipeline start` is the real launch verb,
    so a plan it *accepts* spawns a driver and dispatches a paid agent. A refusal
    happens before the scheduler is reached and costs nothing, which is what makes
    driving the real verb the honest way to measure a load-time contract. Acceptance is
    therefore never measured directly — it is measured as *getting past* the field
    under test to a refusal about something else.
    """
    started = subprocess.run(
        [str(ENGINE), "start", plan, "--detach"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert started.returncode != 0, (
        f"{plan} was accepted and launched a run. Every project this journey drives "
        f"must be refused while it loads: {started.stdout!r}"
    )
    return started.stdout + started.stderr


def test_the_cli_the_manager_verbs_run_carries_the_release_surface() -> None:
    """The pinned CLI can be asked a release question, which is the surface's whole point."""
    adopted = (REPO_ROOT / "config" / "onevcs.version").read_text("utf-8").strip()
    assert Release.parse(adopted, "config/onevcs.version") >= RELEASE_SURFACE_FLOOR, (
        f"config/onevcs.version reads {adopted}, below the {RELEASE_SURFACE_FLOOR} that "
        f"carries the release surface; {GUIDANCE_SECTION!r} in AGENTS.md says the "
        "surface is in force on this host, and that is no longer true"
    )

    reported = _onevcs("--version")
    assert reported.returncode == 0, reported.stderr
    assert reported.stdout.strip() == f"onevcs {adopted}", (
        "the CLI a manager verb runs is not the one config/onevcs.version pins; every "
        "claim this journey makes about the pin is a claim about that copy"
    )

    listed = _onevcs("--help")
    assert listed.returncode == 0, listed.stderr
    commands = listed.stdout.split("Commands:", 1)[1].split("Options:", 1)[0]
    assert re.search(rf"(?m)^\s+{RELEASE_VERB}\b", commands), (
        f"the pinned onevcs lists no `{RELEASE_VERB}` verb group, so no release target "
        f"could be declared on this host at all and {GUIDANCE_SECTION!r} in AGENTS.md "
        "must stop saying the surface is in force here"
    )

    group = _onevcs(RELEASE_VERB, "--help")
    assert group.returncode == 0, group.stderr
    offered = group.stdout.split("Commands:", 1)[1].split("Options:", 1)[0]
    missing = [verb for verb in RELEASE_SUBCOMMANDS if not re.search(rf"(?m)^\s+{verb}\b", offered)]
    assert not missing, (
        f"the pinned onevcs's `{RELEASE_VERB}` group is missing {missing}; AGENTS.md "
        "describes what each of the four verbs is for, and a description of a verb "
        "that is not there is the class of claim this journey exists to catch"
    )


def test_nothing_on_this_host_declares_a_release_target() -> None:
    """The second half of the section's opening sentence, measured rather than assumed.

    "In force" and "in use" are different, and this is the one that decides what a run
    actually does: with no target declared, a dependency landing anywhere earns no
    reference row and no hold, whatever mode a node resolves to. A target appearing
    anywhere is not a defect — it is somebody putting the mechanism to work, and the
    section's claim that nothing does coming due in the same moment.

    Asked of **every** registered identity rather than of this repository alone,
    because that is the breadth of the claim: "no repository registered here declares
    a release target". A version of this that asked only about `ai-orchestrator` would
    pass while a sibling repository held a target and a `published` node really could
    hold — which is the state the section says does not exist.

    "Registered here" is the `onevcs` registry rather than `config/onevcs.checkouts`,
    and the difference is load-bearing between a registration landing in `config/` and
    `just repos-apply` installing it: the tracked list names the identity, the registry
    cannot resolve it, and no plan can dispatch against it either. Such an identity is
    outside the claim rather than an answer this journey declined to get — but that is
    *proven* against the registry's own listing below, not assumed from a refusal,
    because a refusal misread is exactly how an identity that does declare a target
    would go unasked.
    """
    identities = registered_checkouts()
    assert identities, (
        "no registered checkout resolved, so this journey would assert the claim over "
        "an empty set; run `just repos-apply` so the host holds the identities "
        "`config/onevcs.checkouts` lists"
    )

    held = _registry_identities()
    declaring = {}
    unregistered = {}
    for identity in sorted(identities):
        reported = _onevcs(RELEASE_VERB, "targets", identity, "--json")
        if reported.returncode != 0 and NOT_REGISTERED in reported.stderr:
            assert identity not in held, (
                f"{identity} was refused as unregistered while `onevcs repos` lists it, "
                f"so that refusal is about something else: {reported.stderr}"
            )
            unregistered[identity] = reported.stderr.strip()
            continue
        assert reported.returncode == 0, f"{identity}: {reported.stderr}"
        declared = json.loads(reported.stdout)
        if declared["targets"] or declared["adoption"] != DEFAULT_ADOPTION:
            declaring[identity] = declared

    answered = sorted(set(identities) - set(unregistered))
    assert answered, (
        f"this host's `onevcs` registry holds none of the {len(identities)} identities "
        "`config/onevcs.checkouts` names, so the claim would be asserted over an empty "
        f"set; run `just repos-apply`. Awaiting registration: {sorted(unregistered)}"
    )

    assert not declaring, (
        f"these registered repositories declare a release target or a non-default "
        f"adoption rung: {declaring}. {GUIDANCE_SECTION!r} in AGENTS.md says no "
        "repository registered here declares one and that every node therefore "
        "resolves to fast with nothing to await; both sentences are now due"
    )

    # And the refusal an operator meets when they ask anyway, which is what tells them
    # where to declare one. Named in AGENTS.md, so it is read from the CLI rather than
    # restated: a reworded refusal would leave that sentence quoting nothing.
    asked = _onevcs(RELEASE_VERB, "latest", THIS_REPOSITORY)
    assert asked.returncode != 0
    assert "declares no release targets" in asked.stderr, asked.stderr
    assert "release-targets file" in asked.stderr, asked.stderr


def test_the_engine_a_dispatch_runs_links_an_onevcs_that_can_resolve_a_release() -> None:
    """A node's adoption mode resolves through the *linked* onevcs, not the CLI pin.

    `config/onevcs.version` is the CLI a manager verb runs and says nothing about
    this: what a dispatch publishes through, and what an engine asks whether a release
    has happened, is the copy the adopted engine links. Reading it out of the binary is
    what makes the answer independent of every file that merely describes it — the
    distinction this host has twice acted on the wrong side of.
    """
    assert ENGINE.is_file(), (
        f"{ENGINE} is not installed; run `just bootstrap` so this journey reads the "
        "engine a dispatch would run rather than skipping the question"
    )
    # No `onepipeline` command reports what it links, so there is no user-facing
    # interface to drive for this question. The registry paths cargo embeds are the
    # artifact's own answer; they are the measurement `AGENTS.md` hands an operator as
    # `strings | grep`; and `tests/e2e/test_linked_engine_reconciliation_e2e.py` —
    # whose expression this reuses rather than restates — reads them the same way.
    # llmlint: ignore[tests_mirror_real_usage] The binary is the only thing that answers.
    found = LINKED_IN_BINARY.findall(ENGINE.resolve().read_bytes())
    linked = {crate.decode(): version.decode() for crate, version in found}
    carried = linked.get("onevcs")
    assert carried is not None, "the engine binary carries no onevcs registry path"
    assert Release.parse(carried, str(ENGINE)) >= RELEASE_SURFACE_FLOOR, (
        f"the adopted engine links onevcs {carried}, below the "
        f"{RELEASE_SURFACE_FLOOR} that carries the release surface. A dispatch cannot "
        f"resolve a release, so {GUIDANCE_SECTION!r} in AGENTS.md is describing a host "
        "this is not"
    )


def test_the_plan_loader_reads_the_adoption_mode_and_names_its_vocabulary(tmp_path: Path) -> None:
    """`adoption` is a field the engine reads, and its two values are the only two.

    Driven through the engine's own `start` rather than through `just check-plan`,
    which is this repository's reader and not the one a launch uses. A refused plan
    never reaches the scheduler, so this costs no dispatch — the refusal is the
    measurement.
    """
    adopted = (REPO_ROOT / "config" / "onepipeline.version").read_text("utf-8").strip()
    assert Release.parse(adopted, "config/onepipeline.version") >= RELEASE_MODES_FLOOR, (
        f"config/onepipeline.version reads {adopted}, below the {RELEASE_MODES_FLOOR} "
        f"that carries the adoption modes; {GUIDANCE_SECTION!r} in AGENTS.md says they "
        "are in force here"
    )

    said = _refused(_plan(tmp_path, "bad-adoption", [_task("a", adoption="bogus")]))

    assert "unknown variant `bogus`" in said and "`fast` or `published`" in said, (
        "the engine did not refuse an unknown adoption mode by naming the two it takes. "
        f"AGENTS.md quotes that refusal as evidence the field is read: {said!r}"
    )


def test_the_plan_loader_reads_consumes_against_the_nodes_own_dependencies(
    tmp_path: Path,
) -> None:
    """`consumes` is keyed by dependency node id, and the loader holds it to that.

    The keying is the part a plan author gets wrong — two nodes in one repository can
    want different targets, which is why it is not keyed by repository — so the refusal
    that says so is what this measures.
    """
    said = _refused(
        _plan(
            tmp_path,
            "bad-consumes",
            [
                _task("a"),
                _task("b", deps=["a"], adoption="published", consumes={"nosuch": "crate"}),
            ],
        )
    )

    assert "`consumes` names 'nosuch'" in said and "not one of this node's deps" in said, (
        "the engine did not refuse a `consumes` key that names no dependency of the "
        f"node carrying it, which is the keying AGENTS.md describes: {said!r}"
    )
    # And this is where `adoption: published` is proved *accepted*: the refusal above
    # is a semantic check that runs after the document has deserialized, so reaching it
    # at all means the loader took the mode. Measuring acceptance any more directly
    # would mean launching the plan, which is a dispatch nobody asked for.
    assert "adoption" not in said, (
        "the loader refused this plan over its `adoption` field rather than over its "
        f"`consumes` key, so nothing here shows `published` being accepted: {said!r}"
    )


def test_the_read_api_the_view_is_served_from_is_the_adopted_release() -> None:
    """The third half is two artifacts of one release, and this is the one with a CLI.

    `config/onepipeline-ui.version` names a release published as both a wheel and an
    npm package, and only the wheel can be asked its own version. The bundle half is
    read where it is served, in `tests/e2e/test_dag_ui_serving_e2e.py`, which is the
    only place a bundle can honestly be asked anything.
    """
    adopted = (REPO_ROOT / "config" / "onepipeline-ui.version").read_text("utf-8").strip()
    assert Release.parse(adopted, "config/onepipeline-ui.version") >= RELEASE_VIEW_FLOOR, (
        f"config/onepipeline-ui.version reads {adopted}, below the {RELEASE_VIEW_FLOOR} "
        f"that carries the view; {GUIDANCE_SECTION!r} in AGENTS.md says all three "
        "halves of release adoption are in force here"
    )

    reported = subprocess.run(
        [*READ_API, "--version"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert reported.returncode == 0, reported.stderr
    assert reported.stdout.strip() == f"onepipeline-api {adopted}", (
        "the read API `just telemetry-server` runs is not the one "
        f"config/onepipeline-ui.version pins: {reported.stdout.strip()!r}"
    )


def test_the_section_says_so_in_the_words_this_journey_measures() -> None:
    """The measurement is only worth taking while the prose makes the claim it checks."""
    assert IN_FORCE_UNUSED in flat(section()), (
        f"{GUIDANCE_SECTION!r} no longer opens by saying the surface, the modes, and "
        "the view are in force here while nothing on this host uses them. That "
        "sentence is what the "
        "measurements above exist to keep true; without it a reader cannot tell a "
        "capability this host has from one it is using"
    )
