"""The engine this host pins carries every engine-side fix of the plans that changed it.

Nine of the supervision-window plan's nodes landed in `onepipeline` rather than here, and
five of the root-causes plan's after it, and none of them is in force on this host until
`config/onepipeline.version` names a release whose history contains it. The pin is one
number, and a number says nothing about *which* landings it carries: release-plz cuts a
release from whatever is on the base when it cuts it, so a release published between two
of these landings carries some and not the others, reads as current, and leaves a
supervisor believing repairs are in force that nothing installed here can perform.

So the landings are recorded, one row per node, by the commit each produced — and the
adopted release's own history is asked whether it contains them. History rather than a
release note: a note is written by whoever wrote it, and what decides whether a fix is in
a release is whether the commit is an ancestor of that release's tag.

**A landing the adopted release is known not to carry is declared, not omitted.** A
release is cut from whatever is on the base when release-plz cuts it, so the release
adopted on a day a plan's last landing merged can carry every landing but that one; the
pin then reads current while one repair is not in force. `AWAITING_RELEASE` names each
such landing with the release PR that carries it, and the gate holds it to being
*absent* from the adopted release — so the next bump, which will carry it, fails here
until the row is moved into `LANDINGS`, rather than inheriting a declaration that says a
repair is missing when it is not.

**What this cannot say is whether a row is complete.** Nothing here knows the plan; the
rows are what the run that made them recorded, node by node, and a tenth engine node
nobody added would go unnoticed. That is the same gap `tests/test_dated_claims.py`
records about the test a paragraph names, and the reviewer reading the plan beside this
table is the judge of it. What it *can* say — and what no reader can hold in their head —
is that this release carries all nine of these rather than five of them.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
repository runs one Nx project and splits its tiers by pytest marker over four `nx.json`
keys. The subject here is a registered checkout of another repository, which lives outside
this workspace and so outside every one of those keys — hence `reads_checkouts`, the
uncached tier, where a memo cannot replay a green across the upgrade this exists to catch.
A project of its own would need a key over the same nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from published_tools import PUBLISHED_TOOLS
from registered_checkouts import listed_checkout_paths

pytestmark = pytest.mark.reads_checkouts

#: The engine whose history is asked, and the pin that names the release asked about.
ENGINE = "onepipeline"
ENGINE_VERSION_FILE = "onepipeline.version"


class Landing(NamedTuple):
    """One engine-side node of the plan, and the commit its work landed as."""

    #: The node id, as the plan and the run's journal spell it. Retries appended `-2`
    #: and `-3` to that id and are deliberately not recorded: what landed is one commit
    #: per node whatever it took to get there, and a row per attempt would be a record
    #: of this host's scheduling rather than of the engine's history.
    node: str
    #: The change request it published as, for a reader who wants the discussion.
    change_request: int
    #: The commit on the engine's base branch. This is what is asked of the history:
    #: the squash `onevcs` and GitHub between them left, which is the only object that
    #: exists on the base at all — the branch's own commits do not survive the squash.
    commit: str
    #: What that landing did, so a row is legible without opening the change request.
    did: str


#: Every engine-side node of the supervision-window plan, in the order its work landed.
SUPERVISION_WINDOW_LANDINGS = (
    Landing(
        node="op-loader-policy",
        change_request=211,
        commit="6c18f7e",
        did=("refuse at load what a node's publication policy would refuse after the dispatch"),
    ),
    Landing(
        node="op-scheduling",
        change_request=213,
        commit="ab34b1c",
        did="decide a node's dependents from its outcome, not from its status alone",
    ),
    Landing(
        node="op-release-correlation",
        change_request=214,
        commit="a6c2b7d",
        did="record the landing a manager settles from, and latch a hold once satisfied",
    ),
    Landing(
        node="op-state-checkpoint",
        change_request=217,
        commit="4db4e7e",
        did="fold a run's state from a checkpoint instead of replaying its whole journal",
    ),
    Landing(
        node="op-watch-selector",
        change_request=219,
        commit="b685b79",
        did="return on a validated event selector instead of on a clock",
    ),
    Landing(
        node="op-verification-truth",
        change_request=220,
        commit="cae426d",
        did="resolve every platform package before verifying the launcher",
    ),
    Landing(
        node="op-envelope-and-activity",
        change_request=221,
        commit="954f04e",
        did="apply a command envelope whole, and make the records it writes say what they mean",
    ),
    Landing(
        node="op-driver-records",
        change_request=223,
        commit="0d01f56",
        did="guarantee a handed-off edit is applied, and date the surface it hands out",
    ),
    Landing(
        node="engine-5",
        change_request=225,
        commit="4cd9c52",
        did=(
            "serve run listings from the summary document, and report which owned runs "
            "nothing watches"
        ),
    ),
)


#: Every engine-side node of the root-causes plan that the adopted release carries, in the
#: order its work landed. `op-resolve-siblings` settled `no-changes`: the resolutions it
#: was to move had been moved by #235 (`onevcs` 0.21.0 with its testing crate) and #236
#: (`oneagentgraph` 0.3.17 at the time, `onejudge` 0.8.1, `oneharness-core` 0.13.1) before it was
#: dispatched, so its row names the landing that carried the version-control move, which
#: is the one 0.27.2 lacked.
ROOT_CAUSES_LANDINGS = (
    Landing(
        node="op-cross-platform",
        change_request=228,
        commit="6da8d8a",
        did="have a spawned child announce itself instead of waiting on a fixed deadline",
    ),
    Landing(
        node="op-settlement-and-death",
        change_request=229,
        commit="aab7e75",
        did=(
            "clear a park wherever a node settles, and report an empty branch and an "
            "unnamed failure"
        ),
    ),
    Landing(
        node="op-channel-log",
        change_request=227,
        commit="bbee552",
        did="make the surface log the record and the queue a projection of it",
    ),
    Landing(
        node="op-resolve-siblings",
        change_request=235,
        commit="fac6ff7",
        did=(
            "link the onevcs 0.21.0 that gates on the configured policy and reports the "
            "required checks, beside its testing crate"
        ),
    ),
)

#: The engine half of turn provenance: a manager note told from a supervisor turn by the
#: engine, and carried across a publication-failure re-dispatch. Held here because the
#: producer's half — the `origin` stamp `tests/e2e/test_worker_start_directory_e2e.py`
#: observes on a real dispatch — reads as the whole repair while the engine that acts on
#: it is missing.
NOTE_PROVENANCE_LANDINGS = (
    Landing(
        node="op-note-provenance",
        change_request=231,
        commit="2177b9c",
        did="tell a manager note from a supervisor turn, and carry one across a re-dispatch",
    ),
)

#: The engine half of https://github.com/nickderobertis/ai-orchestrator/issues/855's first
#: fix: the settlement write-back's `project copy` allowed a deadline scaled by the items it
#: writes rather than a fixed minute. `root-causes-539-fixes` settled 18/18 with its board
#: behind it because a 34-item copy outgrew that minute, and a pin naming a release without
#: this landing leaves every larger plan's board to the same end.
#: `tests/writeback_budget/test_adopted_engine_bounds_the_writeback_copy_e2e.py` observes
#: the installed engine doing it.
WRITEBACK_BUDGET_LANDINGS = (
    Landing(
        node="op-writeback-budget",
        change_request=248,
        commit="ac35a1b",
        did=("bound the settlement copy per item, and let a launch set the per-item budget"),
    ),
)

#: Every landing the adopted release is held to carry.
LANDINGS = (
    *SUPERVISION_WINDOW_LANDINGS,
    *ROOT_CAUSES_LANDINGS,
    *NOTE_PROVENANCE_LANDINGS,
    *WRITEBACK_BUDGET_LANDINGS,
)


class Awaiting(NamedTuple):
    """One engine-side landing the adopted release is known to leave out."""

    landing: Landing
    #: The release-plz change request that carries it, for whoever moves the pin next.
    release_change_request: int
    #: Why the pin was moved to a release without it.
    because: str


#: The landings the adopted release does not carry, each declared against the release PR
#: waiting on it. Empty, and kept: the declaration shape and the gate that reads it stay
#: so that the next adoption meeting a release cut between two landings can declare the
#: gap without first rebuilding the machinery to declare it in.
AWAITING_RELEASE: tuple[Awaiting, ...] = ()


def _adopted() -> str:
    """The release `config/onepipeline.version` names."""
    tool = next(tool for tool in PUBLISHED_TOOLS if tool.version_file == ENGINE_VERSION_FILE)
    return tool.adopted_version


def _checkout() -> Path:
    """A registered checkout of the engine, or a failure naming how to get one.

    A failure and never a skip, for the reason `tests/test_engine_contracts.py` gives at
    length: a gate that answers "no checkout, nothing to say" is one the whole quality
    tier passes while reconciling nothing.
    """
    for path in listed_checkout_paths():
        if path.name.split("__")[-1] == ENGINE and (path / ".git").exists():
            return path
    raise AssertionError(
        f"no registered checkout of {ENGINE} on this host, so nothing here can ask "
        "whether the adopted release carries this plan's engine-side work. Clone it "
        "into one of the paths `config/onevcs.checkouts` names"
    )


def _git(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(_checkout()), *arguments],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_checkout_knows_the_release_this_host_pins() -> None:
    """The tag has to be there before containment means anything.

    A `git merge-base --is-ancestor` against a ref that does not resolve fails with the
    same status as one that resolves and does not contain the commit, so a stale checkout
    would report every landing missing and send a reader to re-adopt a release that
    already carries them.
    """
    tag = f"v{_adopted()}"

    resolved = _git("rev-parse", "--verify", f"{tag}^{{commit}}")

    assert resolved.returncode == 0, (
        f"the {ENGINE} checkout does not know {tag}, which `config/{ENGINE_VERSION_FILE}` "
        f"names: {resolved.stderr.strip()}. Fetch its tags — the containment below cannot "
        "tell a missing tag from a missing landing"
    )


@pytest.mark.parametrize("landing", LANDINGS, ids=lambda landing: landing.node)
def test_the_adopted_release_contains_the_landing_this_node_produced(landing: Landing) -> None:
    """Each recorded landing is an ancestor of the release this host runs.

    Asked of the history rather than of a release note, and per landing rather than in
    aggregate, so a release cut between two of them names the nodes it left behind
    instead of reporting one number that looks current.
    """
    tag = f"v{_adopted()}"

    known = _git("rev-parse", "--verify", f"{landing.commit}^{{commit}}")
    assert known.returncode == 0, (
        f"the {ENGINE} checkout does not know {landing.commit}, the commit "
        f"{landing.node} landed as (its change request is #{landing.change_request}): "
        f"{known.stderr.strip()}. Fetch that history"
    )

    contained = _git("merge-base", "--is-ancestor", landing.commit, tag)

    assert contained.returncode == 0, (
        f"{ENGINE} {tag} — the release `config/{ENGINE_VERSION_FILE}` names — does not "
        f"contain {landing.commit}, which is where {landing.node} landed "
        f"(#{landing.change_request}: {landing.did}). That work is not in force on this "
        "host, whatever the pin reads: adopt a release cut after it"
    )


@pytest.mark.parametrize("awaiting", AWAITING_RELEASE, ids=lambda awaiting: awaiting.landing.node)
def test_a_landing_declared_as_awaiting_a_release_is_still_absent_from_the_adopted_one(
    awaiting: Awaiting,
) -> None:
    """A declared gap is held to being a gap, so the bump that closes it moves the row.

    The failure this answers is the quiet one: a declaration that a repair is *not* in
    force, inherited across the adoption that put it in force, reads to a supervisor as a
    reason to keep working around a defect the engine no longer has.
    """
    tag = f"v{_adopted()}"
    landing = awaiting.landing

    known = _git("rev-parse", "--verify", f"{landing.commit}^{{commit}}")
    assert known.returncode == 0, (
        f"the {ENGINE} checkout does not know {landing.commit}, the commit "
        f"{landing.node} landed as (#{landing.change_request}): {known.stderr.strip()}. "
        "Fetch that history"
    )

    contained = _git("merge-base", "--is-ancestor", landing.commit, tag)

    assert contained.returncode != 0, (
        f"{ENGINE} {tag} carries {landing.commit} — {landing.node} (#{landing.change_request}: "
        f"{landing.did}) — which AWAITING_RELEASE declares it does not, because "
        f"{awaiting.because}. Move that row into LANDINGS in the same change, and re-read "
        "every sentence that says the engine half of turn provenance is not in force here"
    )
