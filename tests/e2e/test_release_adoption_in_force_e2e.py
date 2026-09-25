"""Prove, against the installed artifacts, what release adoption does here.

`AGENTS.md`'s "Sequencing a node behind a release" used to describe a mechanism no
release this host installed contained, and then — once all three of its halves were
adopted — a host on which nothing used it. It now makes the pair of claims this journey
holds it to: that this host **can** perform them, and that this host's tracked override,
`config/onevcs.releases.yml`, **configures** them — a default target per producer this
host installs, and the `published` rung for this repository. Both are invisible from a
run: a plan of this repository executes identically whether its nodes wait on a release
or adopt a branch, until the day one of them has to, so both are driven here rather than
asserted.

Five things are measured, against the copies that would have to carry them and the
loader that reads a plan:

* the `onevcs` CLI the manager verbs run, which is `config/onevcs.version` and which
  has to carry the `release` verb group for a target to be configurable at all;
* that the same CLI reports **no release targets** for this repository and the
  `published` rung for it, reports the set of registered repositories that do declare
  one, and resolves for each producer this host installs the wheel it installs as that
  producer's default target — which is what makes "a node of this repository waits on
  a release by depending on the producer's node" a measurement rather than an
  assumption. The resolution itself is proven against a scratch state root the
  installer has been applied to, in `tests/e2e/test_repo_registry_apply_e2e.py`; what
  is asked here is whether **this host** resolves it, and that is asserted only once
  the override is in force here. The host adopts it only when `just repos-apply` runs
  there, which is after the change carrying it lands, so a live-host assertion that it
  already resolves would refuse the very publication that introduces or edits the file
  — the same shape as an identity the tracked list names that the registry does not
  yet hold, and given the same treatment: outside the claim, reported as a skip naming
  the remedy, never failed;
* the `onevcs` the adopted engine **links**, which is what a dispatch publishes
  through and what an engine resolving a node's adoption mode calls — read out of the
  engine binary itself, because that is the artifact that runs;
* the plan loader, which has to read `adoption` and `consumes` and refuse a bad value
  of each before anything is dispatched;
* the read API `just telemetry-server` and `just dag-ui` both run, which is the third
  half's own artifact and the one a claim about the view rests on.

The view half is measured further in `tests/dag_ui/test_dag_ui_serving_e2e.py`, because
what a view does is what it serves: that journey starts `just dag-ui` — the same
published binary, with `--ui`, answering the view and its data on one origin — and
reads the release tier off what the reader answers, which is what a rendered row could
only ever be as true as. That the *bundle* draws one is `onepipeline-ui`'s own tier to
hold.

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
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest
from onevcs_state_snapshot import host_root
from project_fixtures import local_project
from registered_checkouts import registered_checkouts
from test_linked_libraries import LINKED_IN_BINARY, Release
from waits import timeout as e2e_timeout

from orchestrator.host_installs import by_producer
from orchestrator.root import REPO_ROOT

#: The release that added `onevcs release` and the release-target document behind it,
#: as https://github.com/nickderobertis/onevcs/pull/78: the one version this repository
#: has to know to say whether the surface is in force.
RELEASE_SURFACE_FLOOR = Release(0, 13, 0)
#: The release that carries the adoption modes —
#: https://github.com/nickderobertis/onepipeline/pull/113 merged, and this is the first
#: `onepipeline` release cut after it.
RELEASE_MODES_FLOOR = Release(0, 13, 0)
#: The release that added the view showing which release carried each landed node, and
#: every release event, as https://github.com/nickderobertis/onepipeline-ui/pull/36.
RELEASE_VIEW_FLOOR = Release(0, 6, 3)

GUIDANCE_SECTION = "## Sequencing a node behind a release"

# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The project edge this
# rule wants is the one the block below answers: this tier is deliberately uncached, so
# `nx affected` skipping it on a project edge is exactly the memo it must not have.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] This marker is not a
# tier the default run hides: `orchestrator:test-checkouts` is a target of this same
# project that runs exactly `-m reads_checkouts`, deliberately uncached, and the uniform
# target set `just check` runs includes it. The rule's remedy — its own project, so `nx
# affected` can skip it — is the opposite of what this tier needs, because its subject is
# state outside the workspace and a memo keyed on this workspace would describe whatever
# that state was when it was recorded. AGENTS.md states that requirement.
pytestmark = pytest.mark.reads_checkouts
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]

#: The verb group release adoption exists to add, and the six subcommands under it.
#: All six, because the section describes what each one is for and a build carrying
#: five of them would leave one of those descriptions about nothing. `discover` and
#: `declaration` joined the four onevcs 0.13.0 shipped when 0.16.x started reading a
#: repository's own `release-targets.toml`, and `declaration` is the one that reads it.
RELEASE_VERB = "release"
RELEASE_SUBCOMMANDS = (
    "targets",
    "discover",
    "latest",
    "status",
    "acknowledge",
    "declaration",
)

#: Which registered identities carry their own `release-targets.toml`, and how many
#: targets each declares. First measured on the adopted onevcs 0.16.2, which is the
#: release that started reading a repository's own declaration beside the host's — under
#: which six of this host's registered repositories began declaring targets without
#: anybody configuring anything here. `onetaskgraph` is the seventh and arrived the same
#: way: its declaration landed as `f42cccc feat: declare the release targets this
#: repository publishes (#127)` on that repository's own base, and this gate is what
#: brought it due here — the publication of an unrelated branch was refused for it,
#: which is the mechanism working rather than a cost of it.
#:
#: Held as the whole mapping rather than as "at least one", for the reason
#: `LINKED_HARNESS_CORES` is: what a reader of `AGENTS.md` acts on is *which* repository
#: has a release to await, and a set that only had to be non-empty would go on passing
#: while the one they care about dropped out. `AGENTS.md`'s "Sequencing a node behind a
#: release" names this same list, so a change upstream comes due in the prose as well.
#:
#: Asserted against each checkout's **fetched remote base** rather than against what
#: `onevcs` answers here now, and the difference is the whole reason this constant is
#: usable at all. `onevcs release targets` reads the publication checkout's working
#: tree, which several managers share: a checkout sitting on somebody's branch answers
#: `unreadable`, and one whose base is simply behind its origin answers `undeclared` —
#: indistinguishable, from here, from a repository that declares nothing. Taken that
#: way this same list measured six declaring identities in one run, one in the next, and
#: none in the publication that was refused for it, while nothing upstream had moved.
#: A remote-tracking ref moves only when somebody fetches, never when a worktree is
#: checked out or reset, so reading the declaration there is a reading of the repository
#: rather than of what another manager's dispatch happens to be doing.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] The tier this constant
# is asserted in is the one the block above already justifies; it declares no marker of
# its own and adds no tier.
DECLARING_IDENTITIES = {
    "github.com/nickderobertis/oneagentgraph": 3,
    "github.com/nickderobertis/oneharness": 6,
    "github.com/nickderobertis/onejudge": 3,
    # llmlint: ignore[expensive_tests_stay_behind_their_own_edge] one map entry, not a new test
    "github.com/nickderobertis/onemessagebus": 5,
    "github.com/nickderobertis/onepipeline": 3,
    "github.com/nickderobertis/onepipeline-ui": 4,
    "github.com/nickderobertis/onetaskgraph": 5,
    "github.com/nickderobertis/onevcs": 4,
    # llmlint: ignore[expensive_tests_stay_behind_their_own_edge] one map entry, not a new test
    "github.com/nickderobertis/printobserver": 17,
}
# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]

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
#: The same repository as the registry's own key, which is what the per-identity loop
#: below reports and so the spelling the declaring-set assertions have to compare against.
THIS_REPOSITORY_IDENTITY = "github.com/nickderobertis/ai-orchestrator"

#: The global rung `config/onevcs.releases.yml` leaves at `onevcs`'s own default, which
#: is the whole reason a plan against any other repository runs as it always did.
DEFAULT_ADOPTION = "fast"
#: The rung the override names for this repository alone: a node here waits for a
#: producer's release rather than adopting its branch, because its `local-direct`
#: policy refuses the draft a `fast` node behind a release would have to open.
THIS_REPOSITORY_ADOPTION = "published"

#: The override this host tracks, and the name `just repos-apply` installs it under in
#: the state root. Whether the installed copy is the tracked one, byte for byte, is
#: what decides whether the host's own resolution is inside the claim.
TRACKED_OVERRIDE = REPO_ROOT / "config" / "onevcs.releases.yml"
INSTALLED_OVERRIDE = "releases.yml"
#: How the installed path is named to an operator: under an xdist worker `host_root()`
#: is the controller's copy of the host's root, so the path itself would name a
#: temporary directory rather than the file `just repos-apply` writes.
INSTALLED_OVERRIDE_NAMED = (
    f"$ONEVCS_HOME/{INSTALLED_OVERRIDE} (~/.onevcs/{INSTALLED_OVERRIDE} with it unset)"
)

#: How `onevcs` refuses a read of a producer whose override names a `default_target`
#: its declaration cannot currently be read to carry — a checkout another manager's
#: dispatch has on a branch of its own, or one whose base declares nothing. The
#: override is right and the checkout is momentarily not, which is the same state as
#: an `unreadable` declaration and is read as one below.
DEFAULT_TARGET_UNREADABLE = "naming default_target"

#: How `onevcs` refuses a repository its registry does not hold. `config/onevcs.checkouts`
#: is what this host *will* register and the registry is what it *has*, and the two part
#: company from the moment a registration lands in `config/` until `just repos-apply`
#: installs it — so a listed identity can be one no `onevcs` verb can resolve at all.
NOT_REGISTERED = "is not a registered repository"

DECLARATION = "release-targets.toml"

#: How a checkout's fetched base is resolved. `origin/HEAD` is what a clone records
#: when it was made, and is asked first because it is the remote's own answer; the two
#: fallbacks are for a checkout that never recorded one — three of this host's do not —
#: and are tried rather than guessed, since a ref that does not resolve is skipped.
REMOTE_BASES = ("origin/HEAD", "origin/main", "origin/master")


class Declaration(TypedDict):
    """What a `release targets` response says about the repository's own declaration.

    `state` is the one field every state carries; each of the others belongs to a
    single state — `reason` to `unreadable`, `looked_in` to `undeclared` — which is
    what the two `NotRequired`s record, so a read of the wrong one is a type error
    here rather than a `KeyError` in the middle of a journey over fourteen identities.
    """

    state: str
    reason: NotRequired[str]
    looked_in: NotRequired[str]


class ReleaseTargets(TypedDict):
    """The `onevcs release targets --json` response, as far as this journey reads it.

    A target carries its own name, style and probe; nothing here reads inside one, so
    they stay mappings and only how many a repository declares is asserted on.
    `default_target` is present only where the override resolves one.
    """

    identity: str
    adoption: str
    default_target: NotRequired[str]
    targets: list[dict[str, object]]
    declaration: Declaration


def _onevcs(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*ONEVCS, *arguments],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _override_not_in_force() -> str | None:
    """Why this host's own resolution is outside the claim, or None while it is inside.

    Read as a file rather than through a verb, so nothing here contacts the host's
    root; and read off `host_root()`, which under an xdist worker is the controller's
    copy — `tests/e2e/test_onevcs_state_snapshot_e2e.py` holds that copy to carrying
    the host's override byte for byte, or to lacking it as the host does, so the
    reading is the host's either way. The sentence names the tracked file, the
    installed path, and the one recipe that reconciles them, because that is what an
    operator reading a skipped test needs to do next.
    """
    installed = host_root() / INSTALLED_OVERRIDE
    if not installed.is_file():
        return (
            f"this host has no release override in force: {INSTALLED_OVERRIDE_NAMED} does "
            "not exist, so every producer resolves no default target and a `published` "
            "node of this repository launched here is held for ever; `just repos-apply` "
            "installs config/onevcs.releases.yml there, and until it has, what this host "
            "resolves is outside the claim"
        )
    if installed.read_bytes() != TRACKED_OVERRIDE.read_bytes():
        return (
            f"the release override in force on this host, {INSTALLED_OVERRIDE_NAMED}, is not "
            "config/onevcs.releases.yml byte for byte, so what a `published` node launched "
            "here resolves is what that other copy says; `just repos-apply` reinstalls the "
            "tracked one, and until it has, what this host resolves is outside the claim"
        )
    return None


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


def _git(checkout: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _fetched_base(checkout: Path) -> str | None:
    """The remote-tracking ref standing for `checkout`'s base branch, or None.

    A remote-tracking ref is the one thing about a shared checkout that a concurrent
    dispatch does not move: it advances when somebody fetches and at no other time, so
    it says what the repository last published rather than what a worktree is currently
    sitting on.
    """
    for candidate in REMOTE_BASES:
        resolved = _git(checkout, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        if resolved.returncode == 0:
            return candidate
    return None


def _declared_at_fetched_base(checkout: Path) -> int | None:
    """How many targets `checkout`'s repository declares at its fetched base.

    None where it declares none — which covers a repository with no declaration at all
    and one whose base this host holds no fetched copy of, because a release this host
    has never fetched the declaration of is one it could not await either.

    The document is handed to a TOML parser rather than scanned, so a `[[target]]`
    inside a comment or a string is not counted and a malformed document fails here
    rather than being silently read as declaring nothing.
    """
    base = _fetched_base(checkout)
    if base is None:
        return None
    shown = _git(checkout, "show", f"{base}:{DECLARATION}")
    if shown.returncode != 0:
        return None
    declared = tomllib.loads(shown.stdout).get("target", [])
    return len(declared) or None


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
        "describes what each of those verbs is for, and a description of a verb "
        "that is not there is the class of claim this journey exists to catch"
    )


def test_which_registered_repositories_declare_a_release_target() -> None:
    """The section's opening claims, measured rather than assumed.

    "In force" and "in use" are different, and this is the one that decides what a run
    actually does: with no target declared for a repository, a dependency landing there
    earns no reference row and no hold, whatever mode a node resolves to. That used to
    be true of every repository registered here, and onevcs 0.16.x ended it without
    anybody configuring anything — a repository's own `release-targets.toml` is now read
    beside the host's document, and six of this host's siblings ship one. So the claim
    the section makes is narrower than it was and this journey measures it as such:
    `ai-orchestrator` declares none, and exactly `DECLARING_IDENTITIES` do.

    What this host's tracked override *configures* over those declarations — the half
    that makes a `published` node of this repository able to finish — is the next
    journey's, because it is inside the claim only once the override is in force here.

    Asked of **every** registered identity rather than of this repository alone,
    because that is the breadth of the claim. A version of this that asked only about
    `ai-orchestrator` would pass while a sibling repository silently gained or lost a
    target and a `published` node's wait changed under a section that still described
    the old set.

    "Registered here" is the `onevcs` registry rather than `config/onevcs.checkouts`,
    and the difference is load-bearing between a registration landing in `config/` and
    `just repos-apply` installing it: the tracked list names the identity, the registry
    cannot resolve it, and no plan can dispatch against it either. Such an identity is
    outside the claim rather than an answer this journey declined to get — but that is
    *proven* against the registry's own listing below, not assumed from a refusal,
    because a refusal misread is exactly how an identity that does declare a target
    would go unasked.

    The declaring set itself is read from each checkout's fetched base and the live
    `onevcs` answer is cross-checked against it, for the reason `DECLARING_IDENTITIES`
    records: several managers share these checkouts, so the working tree `onevcs`
    reads is whatever the last dispatch left there, and the identical question
    answered six, then one, then none across three runs of this journey while nothing
    upstream had moved. What the cross-check keeps is the half a git read cannot give
    — that the surface really does find and count a declaration — and it is made over
    the checkouts that can answer today rather than over all of them, because an
    identity whose checkout is on somebody's branch is reporting on that branch.
    """
    identities = registered_checkouts()
    assert identities, (
        "no registered checkout resolved, so this journey would assert the claim over "
        "an empty set; run `just repos-apply` so the host holds the identities "
        "`config/onevcs.checkouts` lists"
    )

    held = _registry_identities()
    declaring: dict[str, int] = {}
    answered_live: dict[str, int] = {}
    unreadable: dict[str, object] = {}
    unregistered: dict[str, str] = {}
    for identity, checkout in sorted(identities.items()):
        # The declaring set is a git read of the held checkout, made before the registry
        # is asked anything: which repositories declare a target is a fact about their
        # bases, and one this host has not registered yet declares no less for it.
        at_base = _declared_at_fetched_base(checkout)
        if at_base is not None:
            declaring[identity] = at_base
        reported = _onevcs(RELEASE_VERB, "targets", identity, "--json")
        if reported.returncode != 0 and NOT_REGISTERED in reported.stderr:
            assert identity not in held, (
                f"{identity} was refused as unregistered while `onevcs repos` lists it, "
                f"so that refusal is about something else: {reported.stderr}"
            )
            unregistered[identity] = reported.stderr.strip()
            continue
        # `cast` and no runtime validation, because this journey's whole subject is the
        # installed `onevcs`'s own answer: a response that lost one of these keys must
        # fail here as the drift it is, and the reads below raise `KeyError` naming the
        # key. Validating first would turn that into a message about this file instead.
        if reported.returncode != 0 and DEFAULT_TARGET_UNREADABLE in reported.stderr:
            unreadable[identity] = reported.stderr.strip()
            continue
        assert reported.returncode == 0, f"{identity}: {reported.stderr}"
        declared = cast(ReleaseTargets, json.loads(reported.stdout))
        if declared["declaration"]["state"] == "unreadable":
            unreadable[identity] = declared["declaration"]["reason"]
        elif declared["targets"]:
            answered_live[identity] = len(declared["targets"])

    answered = sorted(set(identities) - set(unregistered))
    assert answered, (
        f"this host's `onevcs` registry holds none of the {len(identities)} identities "
        "`config/onevcs.checkouts` names, so the claim would be asserted over an empty "
        f"set; run `just repos-apply`. Awaiting registration: {sorted(unregistered)}"
    )

    assert declaring == DECLARING_IDENTITIES, (
        f"the registered repositories declaring a release target are {declaring}, and "
        f"this repository is written against {DECLARING_IDENTITIES}. "
        f"{GUIDANCE_SECTION!r} in AGENTS.md names that same list and says which of them "
        "a dependency can be awaited in; re-read it against what is really declared. "
        "This is read from each checkout's fetched base, so an identity missing here "
        "either stopped declaring or has not been fetched since it started"
    )
    assert THIS_REPOSITORY_IDENTITY not in declaring, (
        f"{THIS_REPOSITORY_IDENTITY} now declares a release target, so a plan of this "
        f"repository can hold on one. {GUIDANCE_SECTION!r} in AGENTS.md says it declares "
        "none and that a plan of it therefore awaits nothing; both sentences are now due"
    )

    # The surface's own half: wherever a checkout is in a state `onevcs` can read a
    # declaration out of, what it found is what the repository declares. Asserted as
    # agreement rather than as its own list, because the disagreements this leaves out
    # are checkouts another manager's dispatch is working in.
    disagreed = {
        identity: (found, declaring.get(identity))
        for identity, found in answered_live.items()
        if found != declaring.get(identity)
    }
    assert not disagreed, (
        f"`onevcs {RELEASE_VERB} targets` counted {{identity: (it found, the fetched base "
        f"declares)}} {disagreed}. Where it can read a checkout at all, the surface and "
        "the repository have to agree: a count only one of them has is either a "
        "declaration onevcs mis-parses or one that reached the working tree without "
        "reaching the base"
    )
    assert answered_live or unreadable, (
        "no registered checkout answered a declaration at all, so nothing here proves "
        f"`onevcs {RELEASE_VERB} targets` reads one; every identity it could reach "
        "reported declaring nothing while the fetched bases say "
        f"{sorted(DECLARING_IDENTITIES)} do"
    )

    # And the refusal an operator meets when they ask anyway, which is what tells them
    # where to declare one. Named in AGENTS.md, so it is read from the CLI rather than
    # restated: a reworded refusal would leave that sentence quoting nothing.
    asked = _onevcs(RELEASE_VERB, "latest", THIS_REPOSITORY)
    assert asked.returncode != 0
    assert "declares no release targets" in asked.stderr, asked.stderr
    assert "release-targets file" in asked.stderr, asked.stderr


def test_this_host_resolves_what_the_tracked_override_configures() -> None:
    """The override's answer on this host, once `just repos-apply` has put it in force.

    Each producer this host installs resolves the wheel it installs as its default
    target — the row `orchestrator/host_installs.py` gives it — and this repository
    resolves the `published` rung, while every other registered identity stays on the
    global `fast`. Read through the same verb a dispatch resolves it with, because the
    override is a file `onevcs` reads and what it resolves is the only thing a dispatch
    acts on; the resolution itself, over producers declaring what the real ones
    declare, is proven against a scratch root the installer has been applied to in
    `tests/e2e/test_repo_registry_apply_e2e.py`.

    On a host whose installed override is absent, or is not the tracked file byte for
    byte, none of this is inside the claim yet: the host adopts the override only by
    running the apply script after the change carrying it lands, so a failure here
    would refuse exactly that change, on this host and every other, until somebody
    applied it by hand. That state is reported as a skip naming the remedy — the same
    treatment the journey above gives an identity the tracked list names and the
    registry does not yet hold.
    """
    outside = _override_not_in_force()
    if outside is not None:
        pytest.skip(outside)

    held = _registry_identities()
    identities = registered_checkouts()
    resolved: dict[str, ReleaseTargets] = {}
    unreadable: dict[str, str] = {}
    for identity in sorted(identities):
        reported = _onevcs(RELEASE_VERB, "targets", identity, "--json")
        if reported.returncode != 0 and NOT_REGISTERED in reported.stderr:
            assert identity not in held, (
                f"{identity} was refused as unregistered while `onevcs repos` lists it, "
                f"so that refusal is about something else: {reported.stderr}"
            )
            continue
        if reported.returncode != 0 and DEFAULT_TARGET_UNREADABLE in reported.stderr:
            # The override names a default target the checkout cannot currently be read
            # to declare — another manager's branch, or a base behind its origin. The
            # override is right and the checkout momentarily is not.
            unreadable[identity] = reported.stderr.strip()
            continue
        assert reported.returncode == 0, f"{identity}: {reported.stderr}"
        # `cast` for the reason the declaration journey above gives: the installed
        # `onevcs`'s answer is the subject, and a key it lost must fail as drift.
        resolved[identity] = cast(ReleaseTargets, json.loads(reported.stdout))

    assert resolved, (
        "no registered identity answered a resolution at all, so nothing here proves "
        f"the override resolves anything on this host; unreadable: {unreadable}"
    )
    for identity, declared in resolved.items():
        expected_adoption = (
            THIS_REPOSITORY_ADOPTION if identity == THIS_REPOSITORY_IDENTITY else DEFAULT_ADOPTION
        )
        assert declared["adoption"] == expected_adoption, (
            f"{identity} resolves the {declared['adoption']!r} adoption rung rather than "
            f"{expected_adoption!r}. {GUIDANCE_SECTION!r} in AGENTS.md says this repository "
            "waits on a release and every other node here resolves to fast unless its own "
            "plan says otherwise; the installed override is the tracked one, so the pinned "
            "onevcs is not resolving config/onevcs.releases.yml as that file states"
        )
        installed = by_producer(identity)
        if installed is None:
            assert "default_target" not in declared, (
                f"{identity} resolves the default target {declared.get('default_target')!r} "
                "while orchestrator/host_installs.py says this host installs nothing from "
                "it; the override names a producer the table does not"
            )
        else:
            assert declared.get("default_target") == installed.target, (
                f"{identity} resolves the default target {declared.get('default_target')!r} "
                f"rather than {installed.target!r}, which is the wheel this host installs "
                f"({installed.artifact}); a `published` node depending on it would wait on "
                "the wrong artifact, or on nothing"
            )


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
    # `strings | grep`; and `tests/session_setup_pypi/test_linked_engine_reconciliation_e2e.py` —
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
    npm package, and only the wheel can be asked its own version — the wheel that also
    carries the view, since `serve --ui`. The bundle half is read where it is served, in
    `tests/dag_ui/test_dag_ui_serving_e2e.py`, which is the only place a bundle can
    honestly be asked anything.
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
