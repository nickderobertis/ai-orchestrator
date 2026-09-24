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

#: The engine half of the write-back quota plan: the settlement write-back was what spent
#: this host's GraphQL allowance, retrying a projection the store had refused on a timer
#: and copying every node of the plan to change one. `op-incremental-projection`'s work was
#: delivered by its retry, `op-incremental-projection-2`, on the same branch, and is recorded
#: under the node id for the reason `Landing.node` gives.
#: `tests/writeback_budget/test_adopted_engine_projects_incrementally_e2e.py` observes the
#: installed engine doing both.
WRITEBACK_QUOTA_LANDINGS = (
    Landing(
        node="op-refusal-not-retried",
        change_request=285,
        commit="1395d4294c3791562f3acc5cf647188a615ae54d",
        did="report a projection the store refused once, and retry it only when the graph changes",
    ),
    Landing(
        node="op-incremental-projection",
        change_request=287,
        commit="fbb7e6c48d181805336952287664a28be6412ed2",
        did="copy only the nodes that changed, and record every attempt on the run",
    ),
)

#: The engine half of the follow-ups lifecycle plan: a launch names a success hook and a
#: failure hook, and the driver runs the one the run's ending reaches, once. `settle-hooks`'s
#: work was delivered by its retry, `settle-hooks-2`, and is recorded under the node id for
#: the reason `Landing.node` gives. `tests/run_end_hooks/test_run_end_hooks_e2e.py` observes the
#: installed engine firing both through `just orchestrate`.
FOLLOW_UPS_LIFECYCLE_LANDINGS = (
    Landing(
        node="settle-hooks",
        change_request=290,
        commit="1818aec75b87d2b0ce9d25f223004de5071705bc",
        did="run a success hook or a failure hook once when a run ends",
    ),
)

#: The four engine-side nodes of the host-fixes plan. Together they make cached test
#: inputs honest, scope hook idempotency to an ending epoch, carry the linked Claude
#: authentication classifier through a real dispatch, and make a manager's stated
#: landing authoritative everywhere this host reads it. The host journeys exercise the
#: two manager-visible changes; the linked-library gate reads the relink from the wheel.
HOST_FIXES_LANDINGS = (
    Landing(
        node="op-test-inputs-r2",
        change_request=310,
        commit="1b54297711ca640a9db31fc191757e811626efcf",
        did="declare the scripts crate tests drive and serve the label-strict store in process",
    ),
    Landing(
        node="op-hook-epoch-r2",
        change_request=311,
        commit="727a2bad025fca5987c0e7a9f9804f28e837491a",
        did="give each ending a hook epoch when an accepted edit makes the run live again",
    ),
    Landing(
        node="op-relink-oneharness-r2",
        change_request=319,
        commit="59019824d1a753a5ff550c42ed3d2d481db2e545",
        did="drive a Claude login refusal through the linked authentication classifier",
    ),
    Landing(
        node="op-stated-landing-r2",
        change_request=318,
        commit="693a91100744973d7789eb0242de43f205a4d1e0",
        did="make a stated landing authoritative in views, write-back and release evidence",
    ),
)

#: The one engine-side node of the board-reuse plan: the write-back keeps one board item
#: for the life of a node's lineage, editing it on a retry and reopening it when a
#: cancelled node is retried or requeued, instead of minting a sibling per attempt.
#: `tests/writeback_budget/test_lineage_item_reuse_e2e.py` drives the installed engine
#: through it, and `tests/test_engine_contracts.py` holds the stored shape to its source.
LINEAGE_ITEM_LANDINGS = (
    Landing(
        node="op-lineage-item",
        change_request=385,
        commit="1835e1aa2d43736b98559a461114404adaa8e698",
        did="reuse one board item for the life of a node's lineage",
    ),
)

#: The three engine-side nodes of the accepted-follow-ups plan that release anything.
#: Together they make a stopped-then-adopted run read by its adopting driver in
#: `status`, `watch` and `unwatched`, label a hook record from a superseded epoch in
#: `results`, show a release-held node as held rather than queued, keep a `published`
#: node held when a dependency cannot be resolved this pass, and relink the `onevcs`
#: whose `local-direct` squash keeps a branch's closing lines, whose `release status`
#: backfills a landing its publication never saw, and whose merge path can name a host
#: prerequisite — which the engine settles once as `infrastructure-failure` instead of
#: re-dispatching onto the same branch. The plan's fourth engine node, `op-test-fixtures`
#: (#389), is test and CI hygiene that releases nothing a host runs, so it earns no row.
#: `op-views` and `op-release-hold` were delivered by their retries, `op-views-3` and
#: `op-release-hold-4`, and are recorded under the node id for the reason
#: `Landing.node` gives. `tests/e2e/test_adopted_cli_fixes_e2e.py` drives the two of
#: these this host's recipes lean on hardest — the stop-then-adopt read and the closing
#: line a `local-direct` squash keeps.
ACCEPTED_FOLLOW_UPS_LANDINGS = (
    Landing(
        node="op-views",
        change_request=390,
        commit="dcdede84b09495244f35d2e88d461941caf66cde",
        did="read an adopted run as live, label superseded hooks, and report release holds",
    ),
    Landing(
        node="op-release-hold",
        change_request=386,
        commit="a537303244fa462c5ad47b59d90679624c65e85b",
        did="hold a published node whose dependency could not be resolved this pass",
    ),
    Landing(
        node="op-relink-and-route",
        change_request=394,
        commit="03253f06f24f88bdd068774d1ca607fe4e41ee6e",
        did=(
            "relink the onevcs that keeps closing lines and backfills a late landing, and "
            "settle a host-prerequisite refusal without a re-dispatch"
        ),
    ),
)

#: The two engine-side nodes of the worktree-pool plan. Together they let a launch
#: name a persistent maintenance schedule — `--maintenance-config`, which
#: `scripts/onepipeline.sh` hands every `start` as `config/onepipeline.maintenance.yaml`
#: — and hold a queued lifecycle node for its identity's workspace capacity under the
#: `workspace` hold reason, raising `workspace-wait` and requeueing on a `PoolExhausted`
#: refusal rather than settling it `infrastructure-failure`. Both link the `onevcs`
#: 0.27.0 whose pool the file `config/onevcs.workspaces.yml` sizes.
#: `onepipeline-workspace-capacity` was delivered by its retry,
#: `onepipeline-workspace-capacity-5`, and is recorded under the node id for the reason
#: `Landing.node` gives.
WORKTREE_POOL_LANDINGS = (
    Landing(
        node="onepipeline-workspace-capacity",
        change_request=401,
        commit="28fb9884a723226640f89badac1d05ed802bef2f",
        did="hold a node for workspace capacity per identity, and requeue on a pool refusal",
    ),
    Landing(
        node="onepipeline-maintenance-schedule",
        change_request=405,
        commit="759ed787a4cb6007cd796800d1fe253b4d81d960",
        did=(
            "maintain idle pool slots from the idle branch on a persistent every-duration schedule"
        ),
    ),
)

#: The two engine-side nodes of the agent-visibility plan. Together they give every
#: post-launch verb a typed library call — which is what lets `onepipeline-ui` wrap the
#: whole CLI rather than the read surface alone — group a listing by the project its runs
#: were launched from, and stamp every dispatch the engine starts with the
#: `onepipeline.*` history labels and a per-run pointer file, `oneharness-sessions.jsonl`
#: under the run root, which `onepipeline agents` reads back. Without the first, the
#: manager's views here still print one flat list of run ids; without the second, a
#: dispatch's oneharness sessions are in this host's store with nothing naming which run
#: opened them. `tests/dag_ui/test_dag_ui_serving_e2e.py` drives the grouping and the
#: ownership refusal over HTTP, and `tests/test_engine_history_vocabulary.py` holds the
#: label vocabulary this repository's prose names to the engine's own contract text.
AGENT_VISIBILITY_LANDINGS = (
    Landing(
        node="op-sdk-parity",
        change_request=402,
        commit="52e1427b018b50cc18966789d00689a2c449f3cb",
        did="give every post-launch verb a typed library call and group runs by project",
    ),
    Landing(
        node="op-agent-visibility",
        change_request=414,
        commit="0d7b7f2a9fbdcf9e61342697b5f9e009ce3e48f4",
        did=(
            "stamp every dispatch with history labels and a per-run pointer file, and "
            "read the agents it launched"
        ),
    ),
)


#: The four engine-side nodes of the second accepted-follow-ups plan. Together they make
#: the engine own the planner-channel layout and publish it as the document
#: `config/onemessagebus.yaml` links, read an older launch record's bus configuration
#: best-effort, fire the success hook when a settle carries an ended run to a different
#: terminal outcome, count a reused board item once, discard a release-wait surface its
#: hold has outlived, render an amendment as the criteria it replaces, and relink the
#: onevcs, oneagentgraph and onejudge that link the generic bus 0.8.0. `op-run-records`
#: and `op-node-surfaces` were delivered by their retries, `op-run-records-r3` and
#: `op-node-surfaces-r4`, and are recorded under the node id for the reason `Landing.node`
#: gives. `tests/run_end_hooks/test_run_end_hooks_e2e.py` drives the settle that fires the
#: success hook through `just orchestrate`, and
#: `tests/e2e/test_onemessagebus_cli_e2e.py` drives `just channel-reply` over the linked
#: layout.
CHANNEL_AND_RECORDS_LANDINGS = (
    Landing(
        node="op-own-channel",
        change_request=439,
        commit="f4f4f6f2178d08960e6035f2097876ce4ca9bea6",
        did=(
            "own the planner-channel layout and publish it, and read an older record's bus "
            "configuration best-effort"
        ),
    ),
    Landing(
        node="op-run-records",
        change_request=433,
        commit="17248bea967efb5d41176d0a490693a9ef1f4870",
        did=(
            "fire the success hook after a settle completes an ended run, and count a reused "
            "board item once"
        ),
    ),
    Landing(
        node="op-node-surfaces",
        change_request=434,
        commit="6e46fa8d845434afba23ce580afa95b04db63db8",
        did=(
            "discard a release-wait surface its hold has outlived, and render an amendment "
            "as the criteria it replaces"
        ),
    ),
    Landing(
        node="op-relink",
        change_request=445,
        commit="a265bfebebfc386d0c23591710018318668c7c74",
        did="relink onevcs, oneagentgraph, onejudge and the bus onto the bus 0.8.0",
    ),
)


#: The three engine-side nodes of the scripts-audit plan. Together they make the engine
#: own what this repository composed around it in scripts of its own: a harness-neutral
#: stop guard, free space on `host` and `status`, and an `ask` verb; `publish-branch` and
#: `repo-recover` verbs that draft the body the run path drafts; and a relink onto the
#: onevcs and oneagentgraph releases whose `sweep` verbs report as JSON.
#: `tests/unwatched/test_unwatched_and_stop_hook_e2e.py` drives the guard through the
#: `Stop` hook, `tests/e2e/test_publish_branch_e2e.py` lands through `just publish-branch`,
#: and `tests/e2e/test_sweep_e2e.py` reads both sweep reports.
SCRIPTS_AUDIT_LANDINGS = (
    Landing(
        node="op-supervision-verbs",
        change_request=437,
        commit="b1bbb4f2edd580a7368fb053be1337bb810e3a3f",
        did="a harness-neutral stop guard, free space on host and status, and an ask verb",
    ),
    Landing(
        node="op-publication-verbs",
        change_request=448,
        commit="b117e4521b146b5daba63b482ef3eaf4bd85845b",
        did="publish-branch and repo-recover verbs that draft the body the run path drafts",
    ),
    Landing(
        node="op-relink-sweepers",
        change_request=452,
        commit="559b63b42192bbed2e51ab1bd383ee263816e33c",
        did="relink the onevcs and oneagentgraph releases carrying the JSON sweep reports",
    ),
)


#: The engine-side node of the unpublished-and-unfinished plan: every session a node opens
#: carries `run`, `node` and `launcher` labels on its `onevcs` session record.
#: `tests/e2e/test_repositories_field_dispatch_e2e.py` reads them after a real dispatch.
UNPUBLISHED_UNFINISHED_LANDINGS = (
    Landing(
        node="op-session-labels",
        change_request=482,
        commit="a8116795dfe91134542f3cde6d0cf69f9f3d6daa",
        did="label every session a node opens with its run, node and launching session",
    ),
)

#: Every landing the adopted release is held to carry.
LANDINGS = (
    *SUPERVISION_WINDOW_LANDINGS,
    *ROOT_CAUSES_LANDINGS,
    *NOTE_PROVENANCE_LANDINGS,
    *WRITEBACK_BUDGET_LANDINGS,
    *WRITEBACK_QUOTA_LANDINGS,
    *FOLLOW_UPS_LIFECYCLE_LANDINGS,
    *HOST_FIXES_LANDINGS,
    *LINEAGE_ITEM_LANDINGS,
    *ACCEPTED_FOLLOW_UPS_LANDINGS,
    *WORKTREE_POOL_LANDINGS,
    *AGENT_VISIBILITY_LANDINGS,
    *CHANNEL_AND_RECORDS_LANDINGS,
    *SCRIPTS_AUDIT_LANDINGS,
    *UNPUBLISHED_UNFINISHED_LANDINGS,
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
