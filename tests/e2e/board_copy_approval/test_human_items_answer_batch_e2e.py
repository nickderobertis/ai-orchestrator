"""Questions answered as one batch, as a manager records the answers a user gave in chat.

Every question a manager puts to the user is a question item (`AGENTS.md`'s manager loop),
offering choices where a few answers cover it, and the answers are recorded on those items
acting for the user — several at once through `just human-items answer-batch`, which is also
what the browser's batch send writes. This journey raises three questions as a manager
session through `just human-items raise`, answers them in one batch — one by choice, one in
the user's own words, one by both — and reads the result back the ways a manager does: one
`just human-items watch` delivering every answer under one batch id, `just human-items
report` holding none of them open, and `just human-items show` reading each one's choice and
text. Against the installed engine and a runs root of its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from test_board_copy_design_approval_e2e import _recipe
from test_shared_human_item_defaults_e2e import _home

#: The watch's exit status when the session had nothing undelivered.
NOTHING_PENDING = 5

#: The manager session raising the questions and recording the user's answers.
MANAGER = "batch-answering-manager"

#: Two answers a release question offers, and two a region question offers.
RELEASE_CHOICES = [
    {"id": "ship", "title": "Ship it", "description": "Release **today**."},
    {"id": "hold", "title": "Hold it", "description": "Wait for the fix."},
]
REGION_CHOICES = [
    {"id": "east", "title": "East", "description": "Closest to most users."},
    {"id": "west", "title": "West", "description": "Cheapest capacity."},
]


class Question(NamedTuple):
    """One question the manager raises, and the answer the user gives it in chat."""

    title: str
    #: The answers it offers; ``None`` for a question answered only in the user's words.
    choices: list[dict[str, str]] | None
    #: The batch entry's answer: a choice id, the user's own words, or both.
    answer: dict[str, str]


#: Each question by its dedup key.
QUESTIONS = {
    "release": Question("Which release ships?", RELEASE_CHOICES, {"choice": "ship"}),
    "name": Question("What is the service called?", None, {"text": "Aurora"}),
    "region": Question(
        "Which region hosts it?", REGION_CHOICES, {"choice": "west", "text": "for cost"}
    ),
}


def _as_manager(environment: dict[str, str]) -> dict[str, str]:
    return {**environment, "ONEPIPELINE_LAUNCHER_SESSION": MANAGER}


def _raised(environment: dict[str, str], tmp_path: Path, key: str) -> str:
    """Raise the question ``key`` names through the recipe, answering its item id."""
    question = QUESTIONS[key]
    item: dict[str, object] = {
        "schema_version": 1,
        "dedup_key": f"batch:{key}",
        "kind": "question",
        "title": question.title,
        "body": "Answer in a word, or pick one.",
        "priority": "medium",
        "owner": {"session": MANAGER, "run": None, "plan": None},
        "block": {"scope": "nothing"},
        "approval": None,
    }
    if question.choices is not None:
        item["choices"] = question.choices
    request = tmp_path / f"{key}.json"
    request.write_text(json.dumps(item), encoding="utf-8")
    raised = _recipe(environment, "human-items", "raise", "--input", str(request))
    assert raised.returncode == 0, raised.stdout + raised.stderr
    return str(json.loads(raised.stdout)["item"]["item"]["id"])


def _watched(environment: dict[str, str]) -> list[dict[str, object]]:
    """Every event payload one run of the watch recipe prints for the manager session.

    Empty when it printed nothing, which the engine says by exiting :data:`NOTHING_PENDING`.
    """
    watched = _recipe(environment, "human-items", "watch", "--session", MANAGER, "--timeout", "1")
    if watched.returncode == NOTHING_PENDING and not watched.stdout.strip():
        return []
    assert watched.returncode == 0, watched.stdout + watched.stderr
    return [json.loads(line)["payload"] for line in watched.stdout.splitlines() if line.strip()]


# llmlint: ignore[expensive_tests_stay_behind_their_own_edge] This journey is not expensive: about a dozen recipe calls to the installed engine over a temporary item store, reaching no external service, and it shares this tier's `_recipe` and `_home` helpers with the human-item journeys already here, so a project of its own would buy no time and duplicate that key.  # noqa: E501 - a directive is one line
def test_a_batch_of_answers_reaches_the_manager_as_one_delivery_and_closes_each_question(
    tmp_path: Path,
) -> None:
    environment = _as_manager(_home(tmp_path, "batch-answering-host"))
    ids = {key: _raised(environment, tmp_path, key) for key in QUESTIONS}
    raised = _watched(environment)
    assert [payload["action"] for payload in raised] == ["human-item-raised"] * 3, raised
    first = raised[0]["item"]
    assert isinstance(first, dict) and first["choices"] == RELEASE_CHOICES, first

    batch = tmp_path / "answers.json"
    batch.write_text(
        json.dumps(
            {
                "answers": [
                    {"id": ids[key], **question.answer} for key, question in QUESTIONS.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    sent = _recipe(environment, "human-items", "answer-batch", "--input", str(batch))
    assert sent.returncode == 0, sent.stdout + sent.stderr
    receipt = json.loads(sent.stdout)
    assert receipt["refused"] == [], receipt

    delivered = _watched(environment)
    assert [payload["action"] for payload in delivered] == ["human-item-answered"] * 3, delivered
    assert {json.dumps(payload["batch"]) for payload in delivered} == {
        json.dumps({"id": receipt["batch"], "size": 3})
    }, delivered
    answered = {str(payload["item_id"]): payload["resolution"] for payload in delivered}
    for key, question in QUESTIONS.items():
        answer = question.answer
        resolution = answered[ids[key]]
        assert isinstance(resolution, dict), resolution
        assert resolution.get("choice") == answer.get("choice"), (key, resolution)
        assert resolution["text"] == answer.get("text"), (key, resolution)
        assert resolution["actor"] == f"session:{MANAGER}", resolution
    assert _watched(environment) == [], "a second watch delivered the batch again"

    report = _recipe(environment, "human-items", "report")
    assert report.returncode == 0, report.stdout + report.stderr
    assert "0 listed" in report.stdout, report.stdout
    for question in QUESTIONS.values():
        assert question.title not in report.stdout, report.stdout

    shown = {key: _recipe(environment, "human-items", "show", ids[key]) for key in QUESTIONS}
    for read in shown.values():
        assert read.returncode == 0, read.stdout + read.stderr
    assert "answered: Ship it (ship)" in shown["release"].stdout, shown["release"].stdout
    assert "answered: Aurora" in shown["name"].stdout, shown["name"].stdout
    assert "answered: West (west): for cost" in shown["region"].stdout, shown["region"].stdout
