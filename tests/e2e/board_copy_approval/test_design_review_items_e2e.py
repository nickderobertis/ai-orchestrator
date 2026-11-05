"""A plan's design review is one human item, completed like any task, approving what was read.

`just finish-plan` raises the configured `design-review` phase once a plan's board copy
exists: one task, holding that plan, naming its document and the version — the approval key
`orchestrator/design_approval.py` computes — a person is asked to approve. An ordinary
Complete of that task, from `just human-items complete` or the browser's route, runs the
trusted adapter `.onepipeline/config.yaml` configures, which records exactly the record `just
approve-design` records for that stored version, with no key or board copy given at
completion. A document changed since is refused with "Re-read the current document", the
obsolete task dismissed and one raised for the current version; any other failure leaves the
task open and the plan held. `just approve-design` completes the matching task too, and the
launch gate is the same gate either way.

Everything is real but GitHub's Projects API, the loopback double `tests/github_board.py`
serves, and the paid model `reviewed` scripts: the recipes, the installed engine and plan
store, the engine's live human-item store under a runs root of the test's own, and this
host's tracked `.onepipeline/config.yaml`, enabled by a host-local override exactly as a host
enables it.
"""

from __future__ import annotations

import contextlib
import getpass
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from github_board import _serving_board
from served_recipe import serve, stop
from test_board_copy_design_approval_e2e import (
    DESIGN,
    RENDERED_QUALIFIED,
    _copied,
    _drafted_for_the_board,
    _launch_gate,
    _on_the_board,
    _recipe,
)
from waits import timeout as e2e_timeout

from orchestrator import design_approval, design_review
from orchestrator.root import REPO_ROOT

#: The manager session these journeys act as: the owner of every item they raise.
MANAGER = "design-review-manager"

#: Every variable a process reads to decide which session it acts as.
SESSION_VARIABLES = (
    "ONEPIPELINE_LAUNCHER_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: The login the engine attributes a direct or on-behalf write to.
USER = f"user:{getpass.getuser()}"


class Reviewing(NamedTuple):
    """A plan copied onto the board double, with human items on under a runs root of its own."""

    #: The environment every recipe runs in, acting as :data:`MANAGER`.
    environment: dict[str, str]
    #: The board copy's qualified id: the plan a person reads and the plan the task holds.
    home: str
    #: The runs root the engine's human-item store lives beneath.
    runs: Path


# The engine's item records and watch events exactly as its CLI and read API print them,
# decoded and indexed by key. `Any` rather than `orchestrator.human_items.Item` because these
# journeys assert on the wire fields that model deliberately drops — `block`, `owner`,
# `dedup_key`, and `resolution`'s `actor` and `on_behalf_of` — so reading them through the
# host's model would prove the model instead of what the engine recorded and delivered.
WireRecord = dict[str, Any]


def _acting(environment: Mapping[str, str], session: str | None) -> dict[str, str]:
    """``environment`` acting as ``session``, or as no session at all — a person at a shell."""
    acting = {name: value for name, value in environment.items() if name not in SESSION_VARIABLES}
    if session is not None:
        acting["ONEPIPELINE_LAUNCHER_SESSION"] = session
    return acting


@contextlib.contextmanager
def _reviewing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Reviewing]:
    """Draft, review and copy the plan onto the board double, human items on, unapproved.

    Human items are enabled the way a host enables them — a host-local file
    `ONEPIPELINE_CONFIG` names, holding `enabled: true` and nothing else — so the hooks and
    the approval adapter are the tracked `.onepipeline/config.yaml`'s own.
    """
    environment, _, _ = _drafted_for_the_board(tmp_path, monkeypatch, approve=False)
    host = tmp_path / "onepipeline-host.yaml"
    host.write_text("human_items:\n  enabled: true\n", encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    with _serving_board() as remote:
        reviewing = _acting({**environment, **remote}, MANAGER)
        reviewing |= {"ONEPIPELINE_CONFIG": str(host), "ONEPIPELINE_RUNS_DIR": str(runs)}
        copied = _recipe(reviewing, "copy-plan", RENDERED_QUALIFIED, "--to", "plans")
        assert copied.returncode == 0, copied.stdout + copied.stderr
        home = _copied(copied.stdout)[RENDERED_QUALIFIED]
        _on_the_board(monkeypatch, remote)
        yield Reviewing(reviewing, home, runs)


def _raised(reviewing: Reviewing) -> subprocess.CompletedProcess[str]:
    """`just finish-plan`'s design-review step, as the script runs it after the copy."""
    return subprocess.run(
        ["uv", "run", "orchestrator-design-review", "raise", RENDERED_QUALIFIED, "--in", "plans"],
        cwd=REPO_ROOT,
        env=reviewing.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )


def _items(environment: Mapping[str, str], *selectors: str) -> list[WireRecord]:
    """The items `just human-items list --json` answers, each the engine's own item record."""
    listed = _recipe(environment, "human-items", "list", "--json", *selectors)
    assert listed.returncode == 0, listed.stdout + listed.stderr
    return [view["item"] for view in json.loads(listed.stdout)]


def _one_review(reviewing: Reviewing, state: str = "open") -> WireRecord:
    (item,) = [
        item
        for item in _items(reviewing.environment, "--state", state, "--plan", reviewing.home)
        if isinstance(item.get("approval"), dict)
    ]
    return item


def _current_key(home: str) -> str:
    return str(design_approval.current(home)[1])


def _recorded(home: str) -> str | None:
    return design_approval.recorded(design_approval.design_document(home))


def _redesigned(environment: Mapping[str, str], home: str) -> str:
    """Regenerate the authoring document with new prose and copy the plan up again.

    What an edited plan goes through: the document is a rendering, so it is changed by
    rendering it again in place — the store's `document render`, keeping every other answer
    and the copy link — and the board copy changes by `just copy-plan`. Answers the version
    the board copy is at afterwards.
    """
    rendered = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail; "
            'onepipeline template resolve design-doc --json --template-root "$1" | '
            'onetaskgraph document render "$2" --template-loader - --no-interactive --var "$3"',
            "redesign",
            str(REPO_ROOT / "templates"),
            DESIGN,
            "what=The fixture plan, redesigned after a person read it.",
        ],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    copied = _recipe(environment, "copy-plan", RENDERED_QUALIFIED, "--to", "plans")
    assert copied.returncode == 0, copied.stdout + copied.stderr
    return _current_key(home)


def _watched(environment: Mapping[str, str], session: str) -> list[WireRecord]:
    """What ``session``'s next watch prints, once: the item events it has not printed yet."""
    watched = _recipe(environment, "human-items", "watch", "--session", session, "--timeout", "1")
    assert watched.returncode in (0, 5), watched.stdout + watched.stderr
    return [json.loads(line) for line in watched.stdout.splitlines() if line.startswith("{")]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts, configuration and package these journeys
# drive, so narrowing it would memoize a verdict over a tree never run.
# llmlint: ignore-block[e2e_not_mocked] GitHub's Projects API is the one boundary doubled,
# because driving it writes to the live board this repository plans on, and the paid model
# `reviewed` scripts is the other; the recipes, the pinned engine and plan store, the engine's
# item store and the approval adapter it runs are all real.
def test_the_raised_review_holds_the_plan_and_a_manager_completes_it_for_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raise once, hold the plan, complete on the user's behalf: the stored version is approved.

    The task names the board copy and the version its document is at, blocks all of that
    plan, and is raised once however often the step runs. Completing it from the CLI as the
    manager's session — after the user approved in its chat — records the approval of that
    version with no key or board copy given, completes the task, releases the hold and lets
    the launch gate through; the receipt records the session acting on the user's behalf, it
    survives a fresh process reading the store, and reaches the owning session's watch.
    """
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        first = _raised(reviewing)
        again = _raised(reviewing)
        key = _current_key(reviewing.home)
        document_at = design_approval.located(design_approval.design_document(reviewing.home))
        item = _one_review(reviewing)
        report = _recipe(reviewing.environment, "human-items", "report")
        refused_before = _launch_gate(reviewing.environment, reviewing.home)
        assert _recorded(reviewing.home) is None
        completed = _recipe(reviewing.environment, "human-items", "complete", str(item["id"]))
        recorded = _recorded(reviewing.home)
        let_through = _launch_gate(reviewing.environment, reviewing.home)
        after = _recipe(reviewing.environment, "human-items", "report")
        done = _one_review(reviewing, "completed")
        watched = _watched(reviewing.environment, MANAGER)

    assert first.returncode == 0, first.stdout + first.stderr
    assert f"raised {item['id']}" in first.stdout, first.stdout
    assert again.returncode == 0 and "already asks" in again.stdout, again.stdout + again.stderr
    assert item["kind"] == "task" and item["state"] == "open", item
    assert item["approval"] == {"board_copy": reviewing.home, "key": key}, item
    assert item["block"] == {"scope": "plan", "plan": reviewing.home}, item
    assert item["owner"] == {"session": MANAGER, "run": None, "plan": reviewing.home}, item
    assert document_at in str(item["body"]), item
    assert f"blocks all of {reviewing.home}" in report.stdout, report.stdout
    assert refused_before.returncode != 0, refused_before.stdout + refused_before.stderr
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert recorded == key, "completing the task has to record the approval of its stored version"
    assert let_through.returncode == 0, let_through.stdout + let_through.stderr
    assert f"blocks all of {reviewing.home}" not in after.stdout, after.stdout
    assert done["id"] == item["id"], done
    assert done["resolution"] == {
        "actor": f"session:{MANAGER}",
        "on_behalf_of": USER,
        "text": None,
        "reason": None,
    }, done
    kinds = [(event["kind"], event["payload"]["item_id"]) for event in watched]
    assert ("human-item-completed", item["id"]) in kinds, kinds


def test_a_person_completing_directly_is_recorded_as_themself_and_the_owner_still_hears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person at their own shell completes it: their own actor, no on-behalf, owner notified."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        assert _raised(reviewing).returncode == 0
        item = _one_review(reviewing)
        _watched(reviewing.environment, MANAGER)
        person = _acting(reviewing.environment, None)
        completed = _recipe(person, "human-items", "complete", str(item["id"]))
        done = _one_review(reviewing, "completed")
        recorded = _recorded(reviewing.home)
        watched = _watched(reviewing.environment, MANAGER)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert recorded == item["approval"]["key"]
    assert done["resolution"] == {
        "actor": USER,
        "on_behalf_of": None,
        "text": None,
        "reason": None,
    }, done
    kinds = [(event["kind"], event["payload"]["item_id"]) for event in watched]
    assert kinds == [("human-item-completed", item["id"])], kinds


def test_a_changed_document_is_refused_for_re_reading_and_replaced_under_its_new_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completing a task whose document changed records nothing and replaces the task.

    The plan is redesigned and copied up again after the task was raised, and nobody re-ran
    the step: the completion is refused with the engine's re-read words, nothing is approved,
    the obsolete task is dismissed and one task for the current version holds the plan.
    """
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        assert _raised(reviewing).returncode == 0
        stale = _one_review(reviewing)
        moved = _redesigned(reviewing.environment, reviewing.home)
        refused = _recipe(reviewing.environment, "human-items", "complete", str(stale["id"]))
        recorded = _recorded(reviewing.home)
        replacement = _one_review(reviewing)
        dismissed = _one_review(reviewing, "dismissed")
        gated = _launch_gate(reviewing.environment, reviewing.home)

    assert stale["approval"]["key"] != moved
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "Re-read the current document" in refused.stderr, refused.stderr
    assert recorded is None, "a changed document must record no approval"
    assert dismissed["id"] == stale["id"], dismissed
    assert replacement["approval"] == {"board_copy": reviewing.home, "key": moved}, replacement
    assert replacement["block"] == {"scope": "plan", "plan": reviewing.home}, replacement
    assert gated.returncode != 0, gated.stdout + gated.stderr


def test_an_edited_board_copy_retires_the_obsolete_task_when_the_step_runs_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just finish-plan` again after an edit: the old task is dismissed, one is raised anew."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        assert _raised(reviewing).returncode == 0
        old = _one_review(reviewing)
        moved = _redesigned(reviewing.environment, reviewing.home)
        again = _raised(reviewing)
        current = _one_review(reviewing)
        dismissed = _one_review(reviewing, "dismissed")

    assert again.returncode == 0, again.stdout + again.stderr
    assert f"raised {current['id']}" in again.stdout, again.stdout
    assert current["id"] != old["id"], current
    assert current["approval"] == {"board_copy": reviewing.home, "key": moved}, current
    assert str(current["dedup_key"]).endswith(moved), current
    assert dismissed["id"] == old["id"], dismissed
    assert dismissed["resolution"]["reason"] == design_review.OBSOLETE, dismissed


def test_a_failed_approval_leaves_the_task_open_and_the_plan_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The authority cannot reach the store: nothing is approved, completed or released."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        assert _raised(reviewing).returncode == 0
        item = _one_review(reviewing)
    # Outside the served board: its endpoint now refuses every connection.
    refused = _recipe(reviewing.environment, "human-items", "complete", str(item["id"]))
    still = _items(reviewing.environment, "--plan", reviewing.home)
    report = _recipe(reviewing.environment, "human-items", "report")

    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "the task stays open" in refused.stderr, refused.stderr
    assert [one["id"] for one in still] == [item["id"]], still
    assert f"blocks all of {reviewing.home}" in report.stdout, report.stdout


def test_approve_design_completes_the_matching_task_and_releases_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just approve-design` on the current version: the approval and its task, together."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        assert _raised(reviewing).returncode == 0
        item = _one_review(reviewing)
        approved = _recipe(reviewing.environment, "approve-design", reviewing.home)
        done = _one_review(reviewing, "completed")
        recorded = _recorded(reviewing.home)
        gated = _launch_gate(reviewing.environment, reviewing.home)
        report = _recipe(reviewing.environment, "human-items", "report")

    assert approved.returncode == 0, approved.stdout + approved.stderr
    assert "recorded the approval of" in approved.stdout, approved.stdout
    assert f"completed {item['id']}" in approved.stdout, approved.stdout
    assert done["id"] == item["id"], done
    assert recorded == item["approval"]["key"]
    assert gated.returncode == 0, gated.stdout + gated.stderr
    assert f"blocks all of {reviewing.home}" not in report.stdout, report.stdout


def _browser_complete(reviewing: Reviewing, item: str, tmp_path: Path) -> tuple[int, WireRecord]:
    """POST the browser's Complete for ``item`` to `just dag-ui`, as the owning session."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    serving = _acting(reviewing.environment, None)
    serving["CLAUDE_CODE_SESSION_ID"] = MANAGER
    serving["ONEAGENTGRAPH_STATE_DIR"] = str(tmp_path / "graph-records")
    recipe = serve(
        ["just", "dag-ui", "--runs-root", str(reviewing.runs), "--bind", f"127.0.0.1:{port}"],
        serving,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + e2e_timeout(60)
        while True:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=2):
                    break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                assert time.monotonic() < deadline and recipe.poll() is None, "never answered"
                time.sleep(0.1)
        request = urllib.request.Request(
            f"{base}/api/human-items/{item}/complete",
            data=b"{}",
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=e2e_timeout(120)) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as refused:
            return refused.code, json.loads(refused.read() or b"{}")
    finally:
        stop(recipe)


def test_the_browsers_complete_approves_the_stored_version_through_the_same_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The **Action items** page's Complete: `{}` in, the stored version approved, task done."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        assert _raised(reviewing).returncode == 0
        item = _one_review(reviewing)
        status, receipt = _browser_complete(reviewing, str(item["id"]), tmp_path)
        recorded = _recorded(reviewing.home)
        gated = _launch_gate(reviewing.environment, reviewing.home)

    assert status == 200, receipt
    completed = receipt["item"]["item"]
    assert completed["id"] == item["id"] and completed["state"] == "completed", completed
    # The read API records its acting session with the authority's session prefix.
    assert completed["resolution"]["actor"] == f"session:{MANAGER}", completed
    assert completed["resolution"]["on_behalf_of"] is None, completed
    assert recorded == item["approval"]["key"]
    assert gated.returncode == 0, gated.stdout + gated.stderr
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def test_the_tracked_configuration_names_this_repositorys_adapter_and_both_phases() -> None:
    """The effective configuration the engine loads here: both hooks, the one adapter, and the
    shared default turning human items on, projecting tasks into `human-public`."""
    shown = subprocess.run(
        [str(REPO_ROOT / "scripts" / "onepipeline.sh"), "human-items", "config", "--json"],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEPIPELINE_CONFIG": "", "XDG_CONFIG_HOME": "/nonexistent"},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    effective = json.loads(shown.stdout)["human_items"]
    hooks = {hook["phase"]: hook for hook in effective["hooks"]}
    assert hooks[design_review.PHASE]["approval"] == {"adapter": "design-approval"}, hooks
    assert hooks[design_review.PHASE]["block"] == {"scope": "plan"}, hooks
    assert hooks["follow-up-review"]["aggregate"] == "ticket_review", hooks
    assert hooks["follow-up-review"]["block"] == {"scope": "nothing"}, hooks
    adapter = effective["approval_adapters"]["design-approval"]["command"]
    assert adapter[-2:] == ["orchestrator-design-review", "adapter"], adapter
    assert effective["enabled"] is True and effective["task_source"] == "human-public", effective
