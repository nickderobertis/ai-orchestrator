"""What this host's human-item hooks decide, against the released engine's live item store.

The realistic journeys are `tests/e2e/board_copy_approval/test_design_review_items_e2e.py` —
the design review raised, completed from the CLI and the browser, refused for a changed
document, and completed by `just approve-design`, over the real plan store — and the follow-up
review journeys in `tests/e2e/launch_monitor_observer/test_onetaskgraph_host_e2e.py`. What is
proven here is every branch of the host's thin interface, in process: what it hands the engine,
and how it reads each of the engine's answers, including the ones no journey reaches without
breaking a store.

The engine is the real one, run through `scripts/onepipeline.sh` with its live store under a
runs root of each test's own and human items enabled by a host-local file, as a host enables
them; the tracked `.onepipeline/config.yaml` supplies the hooks. The design-approval authority
`orchestrator/design_approval.py` is proven in its own tests and its own journeys, so where a
test here is about how this interface reads the authority's answer, the authority is stood in
for in process — and where a completion has to run a configured adapter, the host-local file
names a stand-in command answering the protocol, as a host may.

llmlint: ignore-file[shell_test_tiers_stay_split] Not a shell suite and not a host tool: the
engine every test here spawns is the workspace's own locked install from `bun.lock`, in this
tier's key, each test's item store is a runs root of its own, and these cover
`orchestrator/human_items.py`, `orchestrator/design_review.py` and the follow-up review step
under the tier's 100% coverage floor, as `tests/test_design_approval.py`'s store tests do.
"""

from __future__ import annotations

import dataclasses
import io
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from orchestrator import design_approval, design_review, follow_up_tickets, human_items
from orchestrator.design_approval import ApprovalKey, Approved
from orchestrator.follow_up_tickets import Priority, ReviewTicket
from orchestrator.plan_store import QualifiedDocumentId, StoreDocument

HOME = "plans:PVT_review_home"
SESSION = "hook-manager"
KEY = ApprovalKey("a" * 64)
MOVED = ApprovalKey("b" * 64)


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, answer: str | None = None) -> Path:
    """Human items on under a runs root of ``tmp_path``'s own, acting as :data:`SESSION`.

    ``answer`` is the key a stand-in approval adapter answers, for a completion that has to
    run one; with none, the tracked adapter stands.
    """
    lines = ["human_items:", "  enabled: true"]
    if answer is not None:
        reply = json.dumps({"board_copy": HOME, "key": answer})
        lines += [
            "  approval_adapters:",
            "    design-approval:",
            f"      command: [{json.dumps(sys.executable)}, '-c', {json.dumps(_stand_in(reply))}]",
        ]
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


def _stand_in(reply: str) -> str:
    return f"import sys; sys.stdin.read(); print({reply!r})"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _store(tmp_path, monkeypatch)


def _document() -> StoreDocument:
    return StoreDocument(
        qualified_id=QualifiedDocumentId(f"{HOME}-design"),
        title="Design: review",
        content="## What\n",
        project="PVT_review_home",
        labels=[],
        repositories=[],
        metadata={},
        location={"url": "https://github.invalid/review/1"},
        version=None,
    )


def _current(
    monkeypatch: pytest.MonkeyPatch, key: ApprovalKey, recorded: str | None = None
) -> None:
    """Stand the authority's reading of the document in: at ``key``, approved at ``recorded``."""
    document = _document()
    if recorded is not None:
        document = dataclasses.replace(
            document, metadata={design_approval.RECORD_KEY: {"key": recorded}}
        )
    monkeypatch.setattr(design_approval, "current", lambda project: (document, key))


def _review(key: ApprovalKey) -> human_items.Item:
    raised = human_items.raise_phase(
        design_review.PHASE,
        {
            "owner": {"session": SESSION, "run": None, "plan": HOME},
            "dedup_key": f"{HOME}:{key}",
            "vars": {"document": "https://github.invalid/review/1"},
            "approval": {"board_copy": HOME, "key": key},
        },
    )
    assert raised is not None
    return raised


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
        "resolution": {"actor": "user:a", "on_behalf_of": None, "text": None, "reason": "gone"},
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


def test_raising_completing_and_dismissing_go_through_the_engine_and_narrow_by_plan(
    store: Path,
) -> None:
    review = _review(KEY)
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
    assert design_review.raise_main(["authoring:review", "--in", "plans"]) == 0
    assert "could not say which session" in capsys.readouterr().err


def test_raising_dismisses_older_versions_and_raises_one_for_the_current(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    older = _review(MOVED)
    _current(monkeypatch, KEY)
    said = design_review.raise_review(HOME, SESSION)
    (current,) = design_review.reviews(HOME)
    assert said.startswith(f"design-review: raised {current.id}")
    assert current.approval == human_items.Approval(HOME, KEY)
    assert _states()[older.id] == "dismissed"
    again = design_review.raise_review(HOME, SESSION)
    assert again.startswith(f"design-review: {current.id} already asks")


def test_a_document_approved_at_this_version_raises_none_and_completes_its_open_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(tmp_path, monkeypatch, answer=KEY)
    review = _review(KEY)
    _current(monkeypatch, KEY, recorded=KEY)
    said = design_review.raise_review(HOME, SESSION)
    assert said.endswith("already approved at this version")
    assert design_review.reviews(HOME) == []
    assert _states()[review.id] == "completed"


def test_a_hook_that_raises_nothing_for_a_review_is_refused(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _current(monkeypatch, KEY)
    monkeypatch.setattr(human_items, "raise_phase", lambda phase, context: None)
    with pytest.raises(OSError, match="raised nothing"):
        design_review.raise_review(HOME, SESSION)


def test_settling_completes_the_approved_version_and_dismisses_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store(tmp_path, monkeypatch, answer=KEY)
    approved, older = _review(KEY), _review(MOVED)
    said = design_review.settle_reviews(HOME, KEY)
    states = _states()
    assert states[approved.id] == "completed"
    assert states[older.id] == "dismissed"
    assert said == [
        f"design-review: completed {approved.id}, releasing {HOME}'s hold",
        f"design-review: dismissed {older.id}, raised for an older version",
    ]


def _approved(key: ApprovalKey, *, held: bool = False, moved: bool = False) -> Approved:
    return Approved(QualifiedDocumentId(f"{HOME}-design"), "here", held=held, key=key, moved=moved)


@pytest.mark.parametrize(
    ("request_text", "refusal"),
    [
        ("[]", "not an object"),
        ('{"board_copy": "plans:x"}', "names no board_copy and key"),
        ('{"board_copy": "plans:x", "key": " "}', "names no board_copy and key"),
        ('{"board_copy": "not qualified", "key": "k"}', "not a qualified project id"),
        ("not json", "Expecting value"),
    ],
)
def test_the_adapter_refuses_a_request_it_cannot_act_on(
    capsys: pytest.CaptureFixture[str], request_text: str, refusal: str
) -> None:
    assert design_review.adapter_main(request_text) == 1
    said = capsys.readouterr()
    assert said.out == "" and refusal in said.err and "nothing was approved" in said.err


def test_the_adapter_approves_the_stored_version_and_answers_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[tuple[str, object]] = []

    def approve(project: str, expected: object = None) -> Approved:
        asked.append((project, expected))
        return _approved(KEY)

    monkeypatch.setattr(design_approval, "approve", approve)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"board_copy": HOME, "key": KEY})))
    assert design_review.adapter_main() == 0
    said = capsys.readouterr()
    assert json.loads(said.out) == {"board_copy": HOME, "key": KEY}
    assert asked == [(HOME, KEY)] and "recorded the approval" in said.err


def test_the_adapter_answers_the_current_version_when_the_document_moved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        design_approval, "approve", lambda project, expected=None: _approved(MOVED, moved=True)
    )
    assert design_review.adapter_main(json.dumps({"board_copy": HOME, "key": KEY})) == 0
    said = capsys.readouterr()
    assert json.loads(said.out) == {"board_copy": HOME, "key": MOVED}
    assert "changed since this task was raised" in said.err


def test_the_adapter_refuses_when_the_authority_does(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refused(project: str, expected: object = None) -> Approved:
        raise OSError("the store could not be read")

    monkeypatch.setattr(design_approval, "approve", refused)
    assert design_review.adapter_main(json.dumps({"board_copy": HOME, "key": KEY})) == 1
    assert "the store could not be read" in capsys.readouterr().err


def test_approve_design_settles_the_reviews_of_the_version_it_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _store(tmp_path, monkeypatch, answer=KEY)
    review = _review(KEY)
    monkeypatch.setattr(design_approval, "approve", lambda project: _approved(KEY))
    assert design_review.approve_main([HOME]) == 0
    said = capsys.readouterr().out
    assert "recorded the approval of" in said and f"completed {review.id}" in said


def test_approve_design_says_nothing_more_with_human_items_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(design_approval, "approve", lambda project: _approved(KEY, held=True))

    def off(home: str, key: ApprovalKey) -> list[str]:
        raise human_items.Unavailable

    monkeypatch.setattr(design_review, "settle_reviews", off)
    assert design_review.approve_main([HOME]) == 0
    assert capsys.readouterr().out.strip().endswith("nothing was recorded")


def test_approve_design_reports_a_review_it_could_not_settle_and_keeps_the_approval(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(design_approval, "approve", lambda project: _approved(KEY))

    def broken(home: str, key: ApprovalKey) -> list[str]:
        raise OSError("the store is gone")

    monkeypatch.setattr(design_review, "settle_reviews", broken)
    assert design_review.approve_main([HOME]) == 0
    assert "could not be completed (the store is gone)" in capsys.readouterr().err


def test_approve_design_refuses_what_the_authority_refuses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert design_review.approve_main(["not-qualified"]) == 1
    assert "not a qualified project id" in capsys.readouterr().err


class _Landed:
    qualified_id = HOME


@pytest.fixture
def landed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(design_review.plan_locations, "copied", lambda project, to: _Landed())
    yield


def test_the_finish_plan_step_raises_for_the_board_copy_the_plan_landed_as(
    store: Path, monkeypatch: pytest.MonkeyPatch, landed: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _current(monkeypatch, KEY)
    assert design_review.main(["raise", "authoring:review", "--in", "plans"]) == 0
    assert "design-review: raised hi-" in capsys.readouterr().out


def test_the_finish_plan_step_never_refuses_the_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    landed: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    off = tmp_path / "off.yaml"
    off.write_text("human_items:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("ONEPIPELINE_CONFIG", str(off))
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", SESSION)
    _current(monkeypatch, KEY)
    assert design_review.raise_main(["authoring:review", "--in", "plans"]) == 0
    assert "human items are off" in capsys.readouterr().out

    def unreadable(project: str) -> tuple[StoreDocument, ApprovalKey]:
        raise OSError("no design document")

    monkeypatch.setattr(design_approval, "current", unreadable)
    assert design_review.raise_main(["authoring:review", "--in", "plans"]) == 0
    assert "no design-review task was raised" in capsys.readouterr().err

    monkeypatch.setattr(human_items, "acting_session", lambda: None)
    assert design_review.raise_main(["authoring:review", "--in", "plans"]) == 0
    assert "acts as no session" in capsys.readouterr().err


def test_the_command_names_its_two_verbs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(design_review, "adapter_main", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["orchestrator-design-review", "adapter"])
    assert design_review.main() == 0
    assert design_review.main(["approve"]) == 2
    assert "usage:" in capsys.readouterr().err


def test_the_installed_command_runs_the_adapter_protocol_end_to_end() -> None:
    """The entry point `.onepipeline/config.yaml` names, refusing a request with no version."""
    ran = subprocess.run(
        ["uv", "run", "--no-sync", "orchestrator-design-review", "adapter"],
        input="{}",
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert ran.returncode == 1 and "nothing was approved" in ran.stderr, ran.stderr


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
