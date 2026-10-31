"""The human-items guidance a manager and a planner read, held to the engine that defines it.

`AGENTS.md` tells a manager how to run the one queue of what people must do — print the
engine's report on every update, put the user's questions through items, keep a worker's
question worker-to-manager, dismiss what nothing needs, reconnect through the watch — and
`personas/planner.yaml` tells a planner how to state a human node's item. Both restate the
engine's own definitions of the four priorities and of a terse item, because a planner
working in another repository reads only its persona; a restatement is a second source, so
each sentence restated is held here to appear word for word in the contract of the engine
`config/onepipeline.version` pins, and fails the day either moves.

The source and blocking wording, and the run-ended reason, are held the same way, since
`docs/orchestration.md` and `docs/dag-ui.md` repeat them to an operator reading a report.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] The
subject is the pinned engine's contract and schema in a registered checkout of another
repository, which lives outside this workspace and so outside every `nx.json` input key —
hence `reads_checkouts`, the uncached tier. `tests/test_engine_contracts.py` and
`tests/test_ui_api_contract.py` carry the same directive for the same reason, and this
module borrows their checkout reader rather than opening a second one.
"""

from __future__ import annotations

import json

import pytest
import yaml
from test_engine_contracts import ONEPIPELINE_DOCS, ONEPIPELINE_SCHEMAS, _source

from orchestrator import human_items
from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The engine's definitions every guidance restates: the four priorities and a terse item.
DEFINITIONS = (
    "Critical means drop everything for complete development disruption or critical "
    "production journeys down with only a human able to fix.",
    "High means substantial development outage, less significant journeys impaired or "
    "partial critical-journey outage needing a human.",
    "Medium means significant lifecycle or experience decisions; Low means everything else.",
    "Bodies are pithy and contain only needed input; question titles ask at high level, "
    "task titles say what to do.",
)

#: How a blocking item says what it holds, and why the engine retires a run's own items.
PRESENTATION = ("blocks N steps in <plan>", "blocks all of <plan>", "blocks all work on this host")
RUN_ENDED = "the run ended"


def _flat(text: str) -> str:
    return " ".join(text.replace("“", '"').replace("”", '"').split())


def _contract() -> str:
    return _flat(_source(ONEPIPELINE_DOCS, "contract.md"))


def _manager() -> str:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    start = text.index("## Your loop as manager")
    return _flat(text[start : text.index("\n## ", start + 1)])


def _planner() -> str:
    persona = yaml.safe_load((REPO_ROOT / "personas" / "planner.yaml").read_text("utf-8"))
    return _flat(str(persona["system_prompt"]))


def _mechanics() -> str:
    return _flat((REPO_ROOT / "docs" / "orchestration.md").read_text(encoding="utf-8"))


@pytest.mark.parametrize("definition", DEFINITIONS)
def test_each_restated_definition_is_the_pinned_engines_own(definition: str) -> None:
    contract = _contract()
    assert definition in contract, (
        f"onepipeline {ONEPIPELINE_DOCS.ref}'s contract no longer says {definition!r}; "
        "re-read its Human items section and restate AGENTS.md and personas/planner.yaml"
    )
    assert definition in _manager(), f"AGENTS.md's manager loop no longer says {definition!r}"
    assert definition in _planner(), f"the planner's persona no longer says {definition!r}"


def test_the_presentation_the_guidance_repeats_is_the_engines() -> None:
    contract, manager, mechanics = _contract(), _manager(), _mechanics()
    for said in PRESENTATION:
        assert f'"{said}"' in contract, said
        assert f'"{said}"' in manager and f'"{said}"' in mechanics, said
    assert f'reason "{RUN_ENDED}"' in contract
    assert f'"{RUN_ENDED}"' in manager and f'"{RUN_ENDED}"' in mechanics
    assert "Source is plan/run, manager session for a question outside a run, or host" in contract
    for source in ("the plan and run", "the manager session for a question", "or the host"):
        assert source in manager and source in mechanics, source


def test_the_manager_prints_the_engines_report_on_every_update() -> None:
    manager = _manager()
    assert "Write every update for a user with limited attention" in manager
    assert (
        "It prints `just human-items report`** — the engine's one report of every open human "
        "item, blocking first, each with its full body — never a list of your own"
    ) in manager
    assert "report is the one printable manager report" in _contract()


def test_the_manager_routes_the_users_questions_through_items_and_keeps_workers_apart() -> None:
    manager = _manager()
    assert "Put a question that is genuinely the user's as a question item where it fits" in (
        manager
    )
    assert "keep unrelated nodes moving while it waits" in manager
    assert "A worker's `ORCHESTRATOR_ASK_MANAGER` question is worker-to-manager" in manager
    assert "relay the answer it records back on that run's channel" in manager


def test_the_manager_acts_for_the_user_only_after_their_decision_and_dismisses_with_reasons() -> (
    None
):
    manager = _manager()
    assert "for the user only after they decided in this chat" in manager
    assert "this session acting for them" in manager
    assert "`just human-items dismiss <item> [--reason TEXT]`" in manager


def test_the_manager_reconnects_through_the_watch_and_the_cli_and_page_share_one_store() -> None:
    manager, mechanics = _manager(), _mechanics()
    assert "reaches your next `just human-items watch` once" in manager
    assert "the browser's **Action items** page acts on the same store" in manager
    assert "the CLI and the page show and do the same things" in mechanics


def test_the_planner_states_every_human_nodes_item_with_an_explicit_priority() -> None:
    planner, manager = _planner(), _manager()
    assert "Plans state `onepipeline.human_item` with an explicit priority on every human node" in (
        _contract()
    )
    assert "State every `kind: human` node's item in `onepipeline.human_item`" in planner
    assert "the `priority` is always explicit" in planner
    for word in ("Critical", "High", "Medium", "Low"):
        assert word in planner and word in manager, word
    assert "a task bound for `plans` carries none, because that source maps none" in planner
    assert "separate from a plan-store task's own priority" in manager


def test_the_item_vocabulary_this_host_parses_is_the_engines_whole_schema() -> None:
    """Every kind, state and priority the pinned engine's schema declares, and no other.

    `orchestrator/human_items.py` refuses an item outside these sets, so an engine that grows
    a member fails here before a host refuses its real items.
    """
    schema = json.loads(_source(ONEPIPELINE_SCHEMAS, "human-items.schema.json"))
    item = schema["HumanItem"]
    declared = {
        name: tuple(member["const"] for member in item["$defs"][name]["oneOf"])
        for name in ("HumanItemKind", "HumanItemState")
    }
    assert declared["HumanItemKind"] == human_items.KINDS
    assert declared["HumanItemState"] == human_items.STATES
    assert tuple(item["properties"]["priority"]["enum"]) == human_items.PRIORITIES
    labels = schema["priority_labels"]
    assert [labels[priority] for priority in human_items.PRIORITIES] == [
        "Critical",
        "High",
        "Medium",
        "Low",
    ], labels
