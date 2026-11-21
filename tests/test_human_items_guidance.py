"""The human-items guidance a manager and a planner read, held to the engine that defines it.

`AGENTS.md` tells a manager how to run the one queue of what people must do — relay its own
items from the engine's report on every update, put the user's questions through items, keep
a worker's question worker-to-manager, dismiss what nothing needs, reconnect through the watch — and
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
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from published_tools import ENGINE_BIN
from test_engine_contracts import (
    ONEPIPELINE_DOCS,
    Engine,
    _pinned_tag,
    _source,
)

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


#: Each property of the manager's update rule for human items, as AGENTS.md words it.
UPDATE_RULE = (
    "It relays only this session's human items**: the items of this session and its runs",
    "plus an item from another session only when it blocks this session's work",
    "Each is one short line saying what is needed, its priority and what it blocks",
    "any link a markdown link named by what it links to",
    "when nothing waits on the user, one line says so",
    "`just human-items report` with no flags is the engine's short view of exactly those items",
    "its short, scoped entries are the summary the update relays",
    "An item's full detail goes to the user only when they ask for it, read with `--full`",
    "`--all` is the whole host's, never the update's",
)


@pytest.mark.parametrize("said", UPDATE_RULE)
def test_the_manager_relays_its_own_items_in_short_lines_read_from_the_report(said: str) -> None:
    manager = _manager()
    assert "Write every update for a user with limited attention" in manager
    assert manager.count(said) == 1, f"AGENTS.md must state {said!r} exactly once"
    whole = _flat((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert whole.count(said) == 1, f"AGENTS.md states {said!r} outside the manager loop"


def test_the_report_the_rule_relays_is_the_pinned_engines_short_session_scoped_view() -> None:
    """The view the rule calls short and scoped is what the pinned engine's `report` prints by
    default, with complete bodies and the whole host each one flag away."""
    shown = subprocess.run(
        [str(ENGINE_BIN), "human-items", "report", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert shown.returncode == 0, shown.stderr
    said = _flat(shown.stdout)
    assert "this session's own and those holding its work" in said, said
    assert "each in brief unless `--full`" in said, said
    assert "Print every item's complete body instead of a one-line description" in said, said
    assert "list every item on the host rather than this session's own" in said, said


def test_no_guidance_still_has_the_manager_paste_the_whole_report() -> None:
    manager, mechanics = _manager(), _mechanics()
    assert "It prints `just human-items report`" not in manager
    assert "the engine's one report of every open human item" not in manager
    assert "each with its full body" not in mechanics
    assert "what a manager prints on every update" not in mechanics
    assert "is where a manager reads the items its update relays" in mechanics
    assert "report is the one printable manager report" in _contract()


#: The pinned engine's library source, where its published question guidance is declared.
ONEPIPELINE_SOURCE = Engine("onepipeline", _pinned_tag("onepipeline"), "src")
QUESTION_GUIDANCE = re.compile(r'pub const QUESTION_GUIDANCE: &str = "((?:[^"\\]|\\.)*)";')

#: Each property of the manager's rule that every question to the user is a question item,
#: as AGENTS.md words it beside the engine's own guidance — which says choices where they fit,
#: and a blocking question tool only where nothing useful can be done and only after the item,
#: and is held word for word below: raised with the recipe and never only in chat, the tool's
#: answer and a chat answer recorded on the item acting for the user, a page batch read as
#: answers, and a worker's question kept worker-to-manager and relayed through an item.
QUESTION_RULE = (
    "**Every question to the user is a question item.** In the engine's words:",
    "Raise it with `just human-items raise`, its `--input` file the engine's item shape, and "
    "never ask it only as a line of chat",
    "Keep supervising while it waits, your watches armed and unrelated nodes moving",
    "Claude Code's built-in question tool is one such harness tool, and its answer is recorded "
    "on the item with `just human-items answer`, acting for the user.",
    "When the user answers in chat, record it with `just human-items answer <item> --choice "
    '<id>` and/or `--text "<their words>"` `--as user`',
    "or `just human-items answer-batch --input <file> --as user` for several, acting for "
    "them as completing a task for them does, so the page and every other manager see it "
    "answered",
    "A batch the user sends from the page arrives as answered items — one `just human-items "
    "watch` delivery listing every answer, or one `human-item` surface on each tied run's "
    "watch — so read it as their answers and never ask again",
    "**It restates every open question item `just human-items report` shows**, until it "
    "leaves the store",
    "the chat holds no question that has no item",
    "**A worker's `ORCHESTRATOR_ASK_MANAGER` question is worker-to-manager**, on that run's "
    "channel: answer it yourself; where only the user can, raise it as a question item, with "
    "choices where they fit, and relay the answer it records back on that run's channel.",
)


@pytest.mark.parametrize("said", QUESTION_RULE)
def test_the_manager_puts_every_question_to_the_user_through_an_item(said: str) -> None:
    manager = _manager()
    assert manager.count(said) == 1, f"AGENTS.md's manager loop must state {said!r} exactly once"
    whole = _flat((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert whole.count(said) == 1, f"AGENTS.md states {said!r} outside the manager loop"


def test_the_manager_restates_the_pinned_engines_question_guidance_exactly_once() -> None:
    """The engine's own sentence, read off the pinned release's source, stands word for word
    in the manager loop and nowhere else in `AGENTS.md`."""
    declared = QUESTION_GUIDANCE.search(_source(ONEPIPELINE_SOURCE, "human_items.rs"))
    assert declared is not None, (
        f"onepipeline {ONEPIPELINE_SOURCE.ref} no longer declares QUESTION_GUIDANCE in "
        "src/human_items.rs; re-read where the engine states it and restate AGENTS.md"
    )
    guidance = _flat(json.loads(f'"{declared.group(1)}"'))
    assert guidance in _contract(), guidance
    whole = _flat((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert _manager().count(guidance) == 1, (
        f"AGENTS.md's manager loop must restate onepipeline {ONEPIPELINE_SOURCE.ref}'s "
        f"QUESTION_GUIDANCE exactly once: {guidance!r}"
    )
    assert whole.count(guidance) == 1, "AGENTS.md restates QUESTION_GUIDANCE twice"


#: Wordings that lead a manager to put a question for the user in chat rather than in an item.
ASKS_IN_CHAT = (
    "ask the user in the chat",
    "put the question in your message",
    "take to the user",
    "decide with the user",
    "decides with the user",
    "that decision with the user",
    "put that question to the user",
    "ask the user before dispatch",
    "unless you are truly blocked",
)


def _guidance_files() -> list[str]:
    """Every file a manager or a dispatch reads guidance from, relative to the checkout."""
    files = [REPO_ROOT / "AGENTS.md", REPO_ROOT / "docs" / "orchestration.md"]
    for directory in ("personas", "templates"):
        files += sorted(path for path in (REPO_ROOT / directory).rglob("*") if path.is_file())
    return [str(path.relative_to(REPO_ROOT)) for path in files]


@pytest.mark.parametrize("relative", _guidance_files())
def test_no_guidance_leads_a_manager_to_ask_the_user_in_chat(relative: str) -> None:
    said = _flat((REPO_ROOT / relative).read_text(encoding="utf-8")).lower()
    asked = [wording for wording in ASKS_IN_CHAT if wording in said]
    assert not asked, (
        f"{relative} still says {asked}; every question to the user is a question item, by "
        "AGENTS.md's manager-loop rule, so point there instead"
    )


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


#: The manager's expiry rule, as AGENTS.md states it once, and the engine words it restates.
EXPIRY_RULE = (
    "**Give every item you raise the expiry that keeps it from going stale**, which retires it "
    "`expired` once it holds: a `time` for a decision with a moment, a `run` or `node` "
    "condition for an item about a run's work, a `check` for one about host state such as "
    "free disk space"
)
EXPIRY_ON_HUMAN_NODES = (
    "Declare expiry on a `kind: human` node only where that node being dropped, and its "
    "dependents proceeding without it, is acceptable."
)
#: Each engine word the rule restates, as the pinned contract spells it.
EXPIRY_WORDS = {
    "`expired`": "`HumanItemState` gains `expired`",
    "`time`": '`{"when":"time",',
    "`run`": '`{"when":"run",',
    "`node`": '`{"when":"node",',
    "`check`": '`{"when":"check",',
}


@pytest.mark.parametrize("said", (EXPIRY_RULE, EXPIRY_ON_HUMAN_NODES))
def test_agents_md_states_the_expiry_rule_exactly_once(said: str) -> None:
    whole = _flat((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))
    assert _manager().count(said) == 1, f"AGENTS.md's manager loop must state {said!r} once"
    assert whole.count(said) == 1, f"AGENTS.md states {said!r} outside the manager loop"


@pytest.mark.parametrize(("word", "defined"), EXPIRY_WORDS.items())
def test_each_expiry_word_the_rule_restates_is_the_pinned_engines(word: str, defined: str) -> None:
    assert word in EXPIRY_RULE
    assert defined in _contract(), (
        f"onepipeline {ONEPIPELINE_DOCS.ref}'s contract no longer defines {word} as {defined!r}; "
        "re-read its Human items Expiry and restate AGENTS.md and docs/orchestration.md"
    )


#: The sentence that introduces `docs/orchestration.md`'s example disk-space raise.
EXAMPLE_LEAD = "This raise expires a disk-space question once `/home` has 20 GiB free again:"


def _example_raise() -> dict[str, object]:
    """The example raise input `docs/orchestration.md`'s Human items section gives."""
    text = (REPO_ROOT / "docs" / "orchestration.md").read_text(encoding="utf-8")
    assert EXAMPLE_LEAD in _flat(text), "docs/orchestration.md lost its example's lead"
    block = re.search(r"again:\n\n```json\n(.*?)\n```", text, re.DOTALL)
    assert block is not None, "docs/orchestration.md lost its example disk-space raise"
    parsed: dict[str, object] = json.loads(block.group(1))
    return parsed


def _check_of(raised: dict[str, object]) -> dict[str, object]:
    expiry = raised["expiry"]
    assert isinstance(expiry, list) and len(expiry) == 1, expiry
    condition: dict[str, object] = expiry[0]
    assert condition["when"] == "check", condition
    return condition


def test_the_documented_disk_space_raise_is_accepted_by_the_pinned_engine(tmp_path: Path) -> None:
    """Fed to the pinned engine's `--sample` raise, the example is accepted and keeps its check."""
    example = _example_raise()
    sample = tmp_path / "sample.json"
    sample.write_text('{"schema_version": 1, "items": [], "approval_keys": {}}', "utf-8")
    raised = tmp_path / "raise.json"
    raised.write_text(json.dumps(example), "utf-8")
    shown = subprocess.run(
        [str(ENGINE_BIN), "human-items", "--sample", str(sample), "raise", "--input", str(raised)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert shown.returncode == 0, shown.stderr
    item = json.loads(shown.stdout)["item"]["item"]
    assert item["state"] == "open", item
    assert item["expiry"] == [_check_of(example)], item["expiry"]


@pytest.mark.parametrize(("threshold_kib", "expired"), (("0", True), (str(2**62), False)))
def test_the_documented_check_reports_by_the_engines_result_protocol(
    threshold_kib: str, expired: bool
) -> None:
    """The example's own check, run as the engine runs it — argv from its `cwd` — exits 0 and
    prints one object whose boolean `expired` says whether the directory's free space meets
    the threshold: zero is always met, 2**62 KiB never is."""
    check = _check_of(_example_raise())
    command = check["command"]
    assert isinstance(command, list) and command[:2] == ["sh", "-c"], command
    argv = [str(part) for part in command[:-1]] + [threshold_kib]
    ran = subprocess.run(
        argv, cwd=str(check["cwd"]), text=True, capture_output=True, check=False, timeout=30
    )
    assert ran.returncode == 0, ran.stderr
    printed = json.loads(ran.stdout)
    assert printed["expired"] is expired, printed
    if expired:
        assert isinstance(printed["reason"], str) and printed["reason"].strip(), printed


#: What `docs/orchestration.md` tells an operator about when an expiry is recorded, each held
#: beside the pinned contract sentence it restates.
LIFECYCLE = {
    "every engine read — `list`, `report`, `show` and `watch`": "so every `list`, `report`, "
    "`show` and `watch`",
    "each live run driver's pass": "and so does each live run driver's pass",
    "`interval_seconds` after the check last ran (default 60, minimum 10, maximum 86400), or at "
    "once if it has never run": '"interval_seconds":<10..=86400>',
    "at most `timeout_seconds` (default 10, maximum 60)": '"timeout_seconds":<1..=60>',
    "older than the timeout plus a 5-second grace": "the check's timeout plus a 5-second grace",
    "Time, run and node conditions** are evaluated in process on every sweep": "Time, run and "
    "node conditions are read in process",
    "only an exit 0 printing exactly one JSON object with a boolean `expired` decides the "
    "outcome": "Exit codes alone never decide the outcome",
    "any other exit, exit 1 included, a signal, a program that cannot start, a `cwd` that does "
    "not exist, its timeout": "any other exit, exit 1 included; a signal",
}
#: The lifecycle sentences that promise no more than the engine and the page deliver.
LIFECYCLE_LIMITS = (
    "the page's live tick, at most every 10 seconds while an Action items view is open",
    "a report taken before the check is next due can still show the item open when its "
    "condition already holds",
    "with nothing reading and no run live, nothing sweeps, so an item of either kind stays open "
    "until the next look",
    "This host's `follow-up-review` hook deliberately carries no expiry",
    "An expired task's board ticket closes as cancelled — not planned, on GitHub Projects — "
    "never as completed",
)


@pytest.mark.parametrize(("said", "defined"), LIFECYCLE.items())
def test_the_operator_lifecycle_restates_the_pinned_engines_expiry(said: str, defined: str) -> None:
    assert _mechanics().count(said) == 1, f"docs/orchestration.md must state {said!r} once"
    assert defined in _contract(), (
        f"onepipeline {ONEPIPELINE_DOCS.ref}'s contract no longer says {defined!r}; re-read its "
        f"Human items Expiry and restate docs/orchestration.md's {said!r}"
    )


@pytest.mark.parametrize("said", LIFECYCLE_LIMITS)
def test_the_operator_lifecycle_states_its_limits(said: str) -> None:
    assert _mechanics().count(said) == 1, f"docs/orchestration.md must state {said!r} once"


def test_the_follow_up_review_hook_carries_no_expiry() -> None:
    config = yaml.safe_load((REPO_ROOT / ".onepipeline" / "config.yaml").read_text("utf-8"))
    (hook,) = [h for h in config["human_items"]["hooks"] if h["phase"] == "follow-up-review"]
    assert "expiry" not in hook, "docs/orchestration.md says the follow-up review never expires"
