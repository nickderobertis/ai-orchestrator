"""What this host's human-item hooks decide, against the released engine's live item store.

The realistic journeys are the follow-up review journeys in
`tests/e2e/launch_monitor_observer/test_onetaskgraph_host_e2e.py`. What is proven here is
every branch of the host's thin interface, in process: what it hands the engine, and how it
reads each of the engine's answers, including the ones no journey reaches without breaking a
store.

The engine is the real one, run through `scripts/onepipeline.sh` with its live store under a
runs root of each test's own and human items enabled by a host-local file, as a host enables
them; the tracked `.onepipeline/config.yaml` supplies the hooks. A plan's design review is
not a hook here: it is the planning run's own `approve-design` human node.

llmlint: ignore-file[shell_test_tiers_stay_split] Not a shell suite and not a host tool: the
engine every test here spawns is the workspace's own locked install from `bun.lock`, in this
tier's key, each test's item store is a runs root of its own, and these cover
`orchestrator/human_items.py` and the follow-up review step under the tier's 100% coverage
floor.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator import follow_up_tickets, human_items
from orchestrator.follow_up_tickets import Priority, ReviewTicket

HOME = "plans:PVT_review_home"
SESSION = "hook-manager"


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Human items on under a runs root of ``tmp_path``'s own, acting as :data:`SESSION`."""
    lines = ["human_items:", "  enabled: true"]
    host = tmp_path / "onepipeline-host.yaml"
    host.write_text("\n".join(lines) + "\n", encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv("ONEPIPELINE_CONFIG", str(host))
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(runs))
    for variable in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", SESSION)
    return runs


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _store(tmp_path, monkeypatch)


def _states() -> dict[str, str]:
    every = json.loads(human_items.verb(["list", "--json", "--state", "open"]))
    every += json.loads(human_items.verb(["list", "--json", "--state", "completed"]))
    every += json.loads(human_items.verb(["list", "--json", "--state", "dismissed"]))
    return {view["item"]["id"]: view["item"]["state"] for view in every}


def test_with_human_items_off_every_verb_answers_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path))
    off = tmp_path / "off.yaml"
    off.write_text("human_items:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("ONEPIPELINE_CONFIG", str(off))
    with pytest.raises(human_items.Unavailable):
        human_items.open_items()


def test_any_other_refusal_names_the_engines_own_words(store: Path) -> None:
    with pytest.raises(OSError, match="refused") as refused:
        human_items.complete("hi-nothing-carries-this")
    assert "human-items complete" in str(refused.value)


def test_a_refusal_with_nothing_on_standard_error_names_its_exit_status(
    store: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    silent = tmp_path / "silent.sh"
    silent.write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
    silent.chmod(0o755)
    monkeypatch.setattr(human_items, "ENGINE", silent)
    with pytest.raises(OSError, match="exit status 3"):
        human_items.verb(["list"])


#: An item the engine answers, with every field this host reads.
ITEM = {
    "id": "hi-1",
    "kind": "task",
    "state": "open",
    "priority": "low",
    "title": "Do the thing",
    "body": "Only what is needed.",
    "approval": None,
    "resolution": None,
}


#: A question, and one answer it may offer, as the engine's schema shapes them.
QUESTION = {**ITEM, "kind": "question", "title": "Which release ships?"}
SHIP = {"id": "ship", "title": "Ship it", "description": "Release **today**."}
ANSWERED = {"actor": "user:a", "on_behalf_of": None, "text": None, "reason": None}


@pytest.mark.parametrize(
    ("answer", "refusal"),
    [
        ("not json", "something other than JSON"),
        ("[]", "names no item"),
        ('{"item": {"blocked": {}}}', "names no item"),
        ('{"item": {"item": []}}', "answered list for an item"),
        (json.dumps({"item": {"item": {**ITEM, "title": " "}}}), "whose title is ' '"),
        (json.dumps({"item": {"item": {**ITEM, "kind": "chore"}}}), "an item of chore"),
        (json.dumps({"item": {"item": {**ITEM, "approval": "k"}}}), "whose approval is 'k'"),
        (
            json.dumps({"item": {"item": {**ITEM, "approval": {"board_copy": "plans:x"}}}}),
            "whose key is None",
        ),
        (json.dumps({"item": {"item": {**ITEM, "resolution": "done"}}}), "resolution is 'done'"),
        (
            json.dumps({"item": {"item": {**ITEM, "resolution": {"reason": 3}}}}),
            "whose reason is 3",
        ),
        (
            json.dumps({"item": {"item": {**ITEM, "resolution": {"verdict": "yes"}}}}),
            "resolution is",
        ),
        (
            json.dumps({"item": {"item": {**ITEM, "resolution": {"text": 3}}}}),
            "whose text is 3",
        ),
        (
            json.dumps({"item": {"item": {**ITEM, "resolution": {"provenance": "planner"}}}}),
            "whose provenance is 'planner'",
        ),
        *(
            (json.dumps({"item": {"item": {**QUESTION, "choices": choices}}}), refusal)
            for choices, refusal in (
                ("ship", "whose choices are 'ship'"),
                ([SHIP] * 7, "whose choices are"),
                (["ship"], "offering the choice 'ship'"),
                ([{**SHIP, "extra": 1}], "offering the choice"),
                ([{**SHIP, "id": "Ship"}], "offering the choice"),
                ([{**SHIP, "title": " "}], "offering the choice"),
                ([{**SHIP, "title": "one\ntwo"}], "offering the choice"),
                ([{**SHIP, "title": "x" * 81}], "offering the choice"),
                ([{**SHIP, "description": ""}], "offering the choice"),
                ([SHIP, SHIP], "offering one choice id twice"),
            )
        ),
        (json.dumps({"item": {"item": {**ITEM, "choices": [SHIP]}}}), "a task offering choices"),
        (
            json.dumps(
                {
                    "item": {
                        "item": {
                            **QUESTION,
                            "choices": [SHIP],
                            "state": "answered",
                            "resolution": {**ANSWERED, "choice": "hold"},
                        }
                    }
                }
            ),
            "answered item whose choice is 'hold'",
        ),
        (
            json.dumps(
                {
                    "item": {
                        "item": {
                            **QUESTION,
                            "choices": [SHIP],
                            "state": "dismissed",
                            "resolution": {**ANSWERED, "choice": "ship"},
                        }
                    }
                }
            ),
            "dismissed item whose choice is 'ship'",
        ),
        (
            json.dumps(
                {
                    "item": {
                        "item": {
                            **QUESTION,
                            "choices": [SHIP],
                            "state": "answered",
                            "resolution": {**ANSWERED, "choice": ["ship"]},
                        }
                    }
                }
            ),
            r"answered item whose choice is \['ship'\]",
        ),
    ],
)
def test_a_receipt_that_is_not_one_is_refused(answer: str, refusal: str) -> None:
    with pytest.raises(OSError, match=refusal):
        human_items.receipt_item(answer)


def test_a_receipt_is_read_into_the_fields_this_host_acts_on() -> None:
    dismissed = {
        **ITEM,
        "state": "dismissed",
        "approval": {"board_copy": "plans:x", "key": "k"},
        "resolution": {
            "actor": "user:a",
            "on_behalf_of": None,
            "text": None,
            "reason": "gone",
            "provenance": "user",
        },
    }
    item = human_items.receipt_item(json.dumps({"item": {"item": dismissed}}))
    assert item == human_items.Item(
        id="hi-1",
        kind="task",
        state="dismissed",
        priority="low",
        title="Do the thing",
        body="Only what is needed.",
        approval=human_items.Approval("plans:x", "k"),
        reason="gone",
    )


@pytest.mark.parametrize(
    ("answer", "refusal"),
    [
        ("not json", "something other than JSON"),
        ('{"items": []}', "is not a list"),
        ('[{"blocked": {}}]', "which is no item view"),
    ],
)
def test_a_list_that_is_not_one_is_refused(
    monkeypatch: pytest.MonkeyPatch, answer: str, refusal: str
) -> None:
    monkeypatch.setattr(human_items, "verb", lambda arguments: answer)
    with pytest.raises(OSError, match=refusal):
        human_items.open_items()


def test_a_question_offering_choices_and_its_answer_are_read_as_the_engine_records_them(
    store: Path, tmp_path: Path
) -> None:
    """The installed engine's own records of a question with choices, open and then answered
    with a choice and the person's words, read through this host's parser."""
    hold = {"id": "hold", "title": "Hold it", "description": "Wait for the fix."}
    request = tmp_path / "question.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dedup_key": "choices:release",
                "kind": "question",
                "title": "Which release ships?",
                "body": "Pick one.",
                "choices": [SHIP, hold],
                "priority": "medium",
                "owner": {"session": SESSION, "run": None, "plan": None},
                "block": {"scope": "nothing"},
                "approval": None,
            }
        ),
        encoding="utf-8",
    )
    raised = human_items.receipt_item(human_items.verb(["raise", "--input", str(request)]))
    offered = (
        human_items.Choice(human_items.ChoiceId("ship"), "Ship it", "Release **today**."),
        human_items.Choice(human_items.ChoiceId("hold"), "Hold it", "Wait for the fix."),
    )
    assert (raised.kind, raised.state, raised.choices, raised.choice) == (
        "question",
        "open",
        offered,
        None,
    )
    assert [item.choices for item in human_items.open_items(kind="question")] == [offered]

    answered = human_items.receipt_item(
        human_items.verb(["answer", raised.id, "--choice", "hold", "--text", "after the fix"])
    )
    assert (answered.state, answered.choices, answered.choice, answered.text) == (
        "answered",
        offered,
        "hold",
        "after the fix",
    )


def test_an_answer_recorded_as_the_users_own_words_is_read_as_the_engine_records_it(
    store: Path, tmp_path: Path
) -> None:
    """A manager relaying the user's answer records it `--as user`, and the installed
    engine's receipt then carries that provenance on the resolution — a field this host's
    parser has to admit, or every answer relayed that way reads as an unreadable item."""
    request = tmp_path / "question.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dedup_key": "provenance:name",
                "kind": "question",
                "title": "What is the service called?",
                "body": "Name it.",
                "priority": "medium",
                "owner": {"session": SESSION, "run": None, "plan": None},
                "block": {"scope": "nothing"},
                "approval": None,
            }
        ),
        encoding="utf-8",
    )
    raised = human_items.receipt_item(human_items.verb(["raise", "--input", str(request)]))
    receipt = human_items.verb(["answer", raised.id, "--text", "Aurora", "--as", "user"])
    assert json.loads(receipt)["item"]["item"]["resolution"]["provenance"] == "user", receipt
    answered = human_items.receipt_item(receipt)
    assert (answered.state, answered.text) == ("answered", "Aurora")


def test_raising_completing_and_dismissing_go_through_the_engine_and_narrow_by_plan(
    store: Path,
) -> None:
    review = human_items.raise_phase(
        "follow-up-review",
        {
            "owner": {"session": SESSION, "run": "run-p", "plan": HOME},
            "dedup_key": "run-p",
            "tickets": [{"id": "t-2", "url": "https://github.invalid/t/2", "priority": "low"}],
        },
    )
    assert review is not None
    finding = human_items.raise_phase(
        "follow-up-review",
        {
            "owner": {"session": SESSION, "run": "run-a", "plan": None},
            "dedup_key": "run-a",
            "tickets": [{"id": "t-1", "url": "https://github.invalid/t/1", "priority": "low"}],
        },
    )
    assert finding is not None
    assert [item.id for item in human_items.open_items(plan=HOME, kind="task")] == [review.id]
    assert human_items.complete(finding.id).state == "completed"
    assert human_items.dismiss(review.id, "withdrawn").reason == "withdrawn"


def test_a_ticket_review_over_no_tickets_raises_nothing(store: Path) -> None:
    context = {"owner": {"session": SESSION, "run": "r", "plan": None}, "dedup_key": "r"}
    assert human_items.raise_phase("follow-up-review", context) is None


def test_the_acting_session_is_the_launcher_sessions_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for variable in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.delenv("ONEPIPELINE_LAUNCHER_SESSION", raising=False)
    assert human_items.acting_session() is None
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "from-the-harness")
    assert human_items.acting_session() == "from-the-harness"


def test_a_session_helper_that_cannot_be_read_is_a_refusal_never_an_absent_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(human_items, "LAUNCHER_SESSION", tmp_path / "no-such-helper.sh")
    with pytest.raises(OSError, match="could not say which session"):
        human_items.acting_session()
    touched = {"followups:I_1": ReviewTicket("https://github.invalid/i/1", Priority.LOW)}
    said = follow_up_tickets.review_raised("run-d", touched)
    assert "was not raised" in said and "could not say which session" in said


class _Landed:
    qualified_id = HOME


def test_the_review_task_is_raised_once_per_run_and_updated_in_place(store: Path) -> None:
    touched = {
        "followups:I_1": ReviewTicket("https://github.invalid/i/1", Priority.HIGH, "Retry drops")
    }
    first = follow_up_tickets.review_raised("run-b", touched)
    touched["followups:I_2"] = ReviewTicket(
        "https://github.invalid/i/2", Priority.NONE, "Watch never re-arms"
    )
    second = follow_up_tickets.review_raised("run-b", touched)
    (task, finding) = sorted(human_items.open_items(), key=lambda item: item.title)
    assert "review task" in first and "at priority medium" in first, first
    assert task.id in second and "2 ticket(s)" in second, second
    # Every ticket is linked by its title, never by its board id or its bare address.
    assert "- [Retry drops](https://github.invalid/i/1)" in task.body, task.body
    assert "No priority\n- [Watch never re-arms](https://github.invalid/i/2)" in task.body
    assert "[Watch never re-arms](https://github.invalid/i/2)" in finding.body, finding.body
    assert "followups:I_" not in task.body + finding.body
    assert follow_up_tickets.review_raised("run-b", {}).endswith("has no review task")
    assert human_items.open_items() == []


def test_the_review_task_is_never_a_reason_to_refuse_a_closeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    touched = {"followups:I_1": ReviewTicket("https://github.invalid/i/1", Priority.LOW)}
    monkeypatch.setattr(human_items, "acting_session", lambda: None)
    assert "acts as no session" in follow_up_tickets.review_raised("run-c", touched)
    monkeypatch.setattr(human_items, "acting_session", lambda: SESSION)

    def off(phase: str, context: object) -> None:
        raise human_items.Unavailable

    monkeypatch.setattr(human_items, "raise_phase", off)
    assert "human items are off" in follow_up_tickets.review_raised("run-c", touched)

    def broken(phase: str, context: object) -> None:
        raise OSError("the store is full")

    monkeypatch.setattr(human_items, "raise_phase", broken)
    assert "was not raised: the store is full" in follow_up_tickets.review_raised("run-c", touched)
