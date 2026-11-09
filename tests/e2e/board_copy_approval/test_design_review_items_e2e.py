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
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml
from github_board import _GitHubFixture, _Operation, _serving_board
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

from orchestrator import design_approval, design_review, plan_store
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


def _checkout_with_credentials(tmp_path: Path, credentials: str) -> Path:
    """A checkout of this tree whose gitignored `.env` holds ``credentials`` and no other's.

    Every other entry links to this tree, so `just dag-ui` run from it is the tracked recipe,
    wrapper and installs. The wrapper reads the `.env` beside its own script by the path it
    was started at, which is this directory, and this tree's own `.env` is never written.
    """
    checkout = tmp_path / "checkout-with-credentials"
    checkout.mkdir(parents=True)
    for entry in REPO_ROOT.iterdir():
        if entry.name != ".env":
            (checkout / entry.name).symlink_to(entry)
    (checkout / ".env").write_text(credentials, encoding="utf-8")
    return checkout


def _browser_complete(
    reviewing: Reviewing,
    item: str,
    tmp_path: Path,
    *,
    checkout: Path = REPO_ROOT,
    token: str | None = "fixture-token",
) -> tuple[int, WireRecord]:
    """POST the browser's Complete for ``item`` to `just dag-ui`, as the owning session.

    Served from ``checkout`` with ``token`` as the environment's board credential, or with
    none defined at all.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    serving = _acting(reviewing.environment, None)
    serving["CLAUDE_CODE_SESSION_ID"] = MANAGER
    serving["ONEAGENTGRAPH_STATE_DIR"] = str(tmp_path / "graph-records")
    serving.pop("GH_PROJECTS_TOKEN", None)
    if token is not None:
        serving["GH_PROJECTS_TOKEN"] = token
    recipe = serve(
        [
            "just",
            "--justfile",
            str(checkout / "justfile"),
            "--working-directory",
            str(checkout),
            "dag-ui",
            "--runs-root",
            str(reviewing.runs),
            "--bind",
            f"127.0.0.1:{port}",
        ],
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


def test_the_served_page_approves_with_the_credential_only_the_checkouts_env_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Started with no board token, the server takes `.env`'s, and the approval records.

    The same item, refused first by a server whose `.env` holds no value for the token, is
    what shows the credential the second server loaded is what recorded the approval.
    """
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        assert _raised(reviewing).returncode == 0
        item = _one_review(reviewing)
        without = _checkout_with_credentials(tmp_path / "without", "GH_PROJECTS_TOKEN=\n")
        refused, refusal = _browser_complete(
            reviewing, str(item["id"]), tmp_path, checkout=without, token=None
        )
        refused_recorded = _recorded(reviewing.home)
        still_open = _one_review(reviewing)
        holding = _checkout_with_credentials(
            tmp_path / "holding", "GH_PROJECTS_TOKEN=fixture-token\n"
        )
        status, receipt = _browser_complete(
            reviewing, str(item["id"]), tmp_path, checkout=holding, token=None
        )
        recorded = _recorded(reviewing.home)
        gated = _launch_gate(reviewing.environment, reviewing.home)

    assert refused != 200, refusal
    assert refused_recorded is None
    assert still_open["id"] == item["id"], still_open
    assert status == 200, receipt
    assert receipt["item"]["item"]["state"] == "completed", receipt
    assert recorded == item["approval"]["key"]
    assert gated.returncode == 0, gated.stdout + gated.stderr


def test_the_served_page_keeps_a_token_the_environment_defines_over_the_checkouts_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `.env` holding no value cannot blank the board token the server was started with."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        assert _raised(reviewing).returncode == 0
        item = _one_review(reviewing)
        checkout = _checkout_with_credentials(tmp_path, "GH_PROJECTS_TOKEN=\n")
        status, receipt = _browser_complete(reviewing, str(item["id"]), tmp_path, checkout=checkout)
        recorded = _recorded(reviewing.home)

    assert status == 200, receipt
    assert receipt["item"]["item"]["state"] == "completed", receipt
    assert recorded == item["approval"]["key"]


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
    # The read API records its acting session under the browser's own actor prefix.
    assert completed["resolution"]["actor"] == f"browser:session:{MANAGER}", completed
    assert completed["resolution"]["on_behalf_of"] is None, completed
    assert recorded == item["approval"]["key"]
    assert gated.returncode == 0, gated.stdout + gated.stderr


def _undo(reviewing: Reviewing, key: str) -> subprocess.CompletedProcess[str]:
    """The adapter's undo as the engine runs it: the tracked command, the stored version."""
    configured = yaml.safe_load((REPO_ROOT / ".onepipeline" / "config.yaml").read_text("utf-8"))
    return subprocess.run(
        configured["human_items"]["approval_adapters"]["design-approval"]["undo"],
        input=json.dumps(
            {"board_copy": reviewing.home, "key": key, "actor": USER, "on_behalf_of": None}
        ),
        cwd=REPO_ROOT,
        env=reviewing.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _completed_review(reviewing: Reviewing) -> WireRecord:
    """Raise the plan's review and Complete it, so its approval is recorded."""
    assert _raised(reviewing).returncode == 0
    item = _one_review(reviewing)
    completed = _recipe(reviewing.environment, "human-items", "complete", str(item["id"]))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return item


def _store(environment: Mapping[str, str], *arguments: str) -> None:
    """One plan-store write, by the locked CLI, as a launch's projection makes it."""
    done = subprocess.run(
        ["onetaskgraph", *arguments],
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_reopening_a_completed_review_takes_its_approval_back_until_it_is_approved_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reopen removes the record and returns the hold; Complete and `approve-design` restore it.

    The tracked `undo` is what the installed engine runs. Repeating it changes nothing, and a
    version the record is not at leaves the record alone.
    """
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
        key = str(item["approval"]["key"])
        assert _recorded(reviewing.home) == key
        reopened = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        removed = _recorded(reviewing.home)
        back_open = _one_review(reviewing)
        held = _recipe(reviewing.environment, "human-items", "report")
        gated = _launch_gate(reviewing.environment, reviewing.home)
        before_repeat = design_approval.design_document(reviewing.home).metadata
        repeated = _undo(reviewing, key)
        after_repeat = design_approval.design_document(reviewing.home).metadata

        again = _recipe(reviewing.environment, "human-items", "complete", str(item["id"]))
        approved_again = _recorded(reviewing.home)
        let_through = _launch_gate(reviewing.environment, reviewing.home)
        other_version = _undo(reviewing, "a-version-the-record-is-not-at")
        left_alone = _recorded(reviewing.home)

        reopened_again = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        by_hand = _recipe(reviewing.environment, "approve-design", reviewing.home)
        approved_by_hand = _recorded(reviewing.home)
        done = _one_review(reviewing, "completed")

    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    assert removed is None, "reopening has to take the approval back"
    assert back_open["id"] == item["id"] and back_open["resolution"] is None, back_open
    assert f"blocks all of {reviewing.home}" in held.stdout, held.stdout
    assert gated.returncode != 0, gated.stdout + gated.stderr
    assert "carries no approval" in gated.stderr, gated.stderr
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert json.loads(repeated.stdout) == {"board_copy": reviewing.home, "key": key}
    assert after_repeat == before_repeat, "a second undo has to change nothing"
    assert again.returncode == 0, again.stdout + again.stderr
    assert approved_again == key
    assert let_through.returncode == 0, let_through.stdout + let_through.stderr
    assert other_version.returncode == 0, other_version.stdout + other_version.stderr
    assert left_alone == key, "an undo for another version leaves the record alone"
    assert reopened_again.returncode == 0, reopened_again.stdout + reopened_again.stderr
    assert by_hand.returncode == 0, by_hand.stdout + by_hand.stderr
    assert approved_by_hand == key
    assert done["id"] == item["id"], done


# llmlint: ignore-block[tests_mirror_real_usage] The criteria require the refusal proven for each
# launch signal alone; one real launch writes them together, so these seed exactly one each,
# in the store and runs root the engine reads, and the real launch is the journey below.
def _driver_claimed(reviewing: Reviewing) -> None:
    claim = json.dumps({"run": "a-run", "host": "a-host", "session": MANAGER})
    _store(
        reviewing.environment,
        "project",
        "metadata",
        "set",
        reviewing.home,
        design_approval.DRIVER_CLAIM,
        claim,
    )


def _task_past_todo(reviewing: Reviewing) -> None:
    task = plan_store.read_tasks(reviewing.home)[0].qualified_id
    _store(reviewing.environment, "task", "status", "set", task, "in-progress")


def _run_named_it(reviewing: Reviewing) -> None:
    run = reviewing.runs / "a-run"
    run.mkdir()
    (run / design_approval.LAUNCH_RECORD).write_text(
        json.dumps({"run_id": "a-run", "project": reviewing.home}), encoding="utf-8"
    )
    # llmlint: ignore-end[tests_mirror_real_usage]


@pytest.mark.parametrize(
    "launch",
    [_driver_claimed, _task_past_todo, _run_named_it],
    ids=["driver-claim", "task-past-todo", "launch-record"],
)
def test_a_launched_plan_refuses_the_reopen_and_keeps_its_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launch: Callable[[Reviewing], None]
) -> None:
    """Each way a plan is known to have launched refuses, saying why, and changes nothing."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
        launch(reviewing)
        refused = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        recorded = _recorded(reviewing.home)
        still = _one_review(reviewing, "completed")

    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "has already launched" in refused.stderr, refused.stderr
    assert "would not stop it" in refused.stderr, refused.stderr
    assert recorded == item["approval"]["key"]
    assert still["id"] == item["id"], still


def _every_task_at(reviewing: Reviewing, status: str) -> None:
    for task in plan_store.read_tasks(reviewing.home):
        _store(reviewing.environment, "task", "status", "set", task.qualified_id, status)


def test_a_plan_whose_tasks_no_run_claimed_can_be_reopened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A status before `todo` is one no run wrote, so the approval comes back off.

    `backlog` is the one a `plans` task can hold; that source maps no option for `draft`.
    """
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
        _every_task_at(reviewing, "backlog")
        reopened = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        recorded = _recorded(reviewing.home)

    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    assert recorded is None


def test_a_cancelled_task_refuses_the_reopen_as_launched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled task is past `todo`, so its plan reads as launched."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
        _every_task_at(reviewing, "cancelled")
        refused = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        recorded = _recorded(reviewing.home)

    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "has already launched" in refused.stderr, refused.stderr
    assert recorded == item["approval"]["key"]


def test_a_task_needing_attention_is_decided_by_the_other_launch_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`unknown` says nothing alone: unestablished by itself, launched beside a run naming it."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
        _every_task_at(reviewing, "unknown")
        alone = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        _run_named_it(reviewing)
        corroborated = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        recorded = _recorded(reviewing.home)

    assert alone.returncode != 0, alone.stdout + alone.stderr
    assert "could not be established" in alone.stderr, alone.stderr
    assert corroborated.returncode != 0, corroborated.stdout + corroborated.stderr
    assert "has already launched" in corroborated.stderr, corroborated.stderr
    assert recorded == item["approval"]["key"]


def test_a_launch_record_that_cannot_be_read_refuses_the_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run's record nothing can read leaves whether the plan launched open, so it refuses."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
        run = reviewing.runs / "a-run"
        # llmlint: ignore-block[tests_mirror_real_usage] No interface writes an unreadable launch
        # record; disk damage is the state under test, and only writing it here reaches it.
        run.mkdir()
        (run / design_approval.LAUNCH_RECORD).write_text("{not json", encoding="utf-8")
        # llmlint: ignore-end[tests_mirror_real_usage]
        refused = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        recorded = _recorded(reviewing.home)
        still = _one_review(reviewing, "completed")

    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "could not be established" in refused.stderr, refused.stderr
    assert design_approval.LAUNCH_RECORD in refused.stderr, refused.stderr
    assert recorded == item["approval"]["key"]
    assert still["id"] == item["id"], still


def test_a_store_read_that_fails_refuses_the_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The board cannot be reached: whether it launched is unknown, so the item stays completed."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
    # Outside the served board: its endpoint now refuses every connection.
    refused = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
    still = _one_review(reviewing, "completed")

    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "could not be established" in refused.stderr, refused.stderr
    assert still["id"] == item["id"], still


def test_a_really_launched_plan_refuses_the_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`just orchestrate` launches the approved plan: what the engine wrote refuses the undo.

    Every task is parked, so nothing dispatches, and no record here is written by the test:
    the launch record and any claim or status are the installed engine's own.
    """
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
        for task in plan_store.read_tasks(reviewing.home):
            _store(
                reviewing.environment,
                *("task", "metadata", "set", task.qualified_id, "onepipeline.parked", "true"),
            )
        launched = _recipe(
            reviewing.environment,
            *("orchestrate", reviewing.home, "--dag-graph", "off"),
            *("--success-hook=", "--failure-hook="),
        )
        records = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in reviewing.runs.glob(f"*/{design_approval.LAUNCH_RECORD}")
        ]
        refused = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        recorded = _recorded(reviewing.home)
        still = _one_review(reviewing, "completed")
        for record in records:
            stopped = _recipe(reviewing.environment, "stop", str(record["run_id"]))
            assert stopped.returncode == 0, stopped.stdout + stopped.stderr

    assert launched.returncode in (0, 3), launched.stdout + launched.stderr
    assert [record["project"] for record in records] == [reviewing.home], records
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "has already launched" in refused.stderr, refused.stderr
    assert "would not stop it" in refused.stderr, refused.stderr
    assert recorded == item["approval"]["key"]
    assert still["id"] == item["id"], still


def test_a_document_edited_during_the_undo_keeps_its_approval_until_the_undo_is_run_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An edit landing after the undo read the document refuses it; running it again removes it."""
    with _reviewing(tmp_path, monkeypatch) as reviewing:
        item = _completed_review(reviewing)
        endpoint = reviewing.environment["ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT"]
        token = reviewing.environment["GH_PROJECTS_TOKEN"]

        def on_github(document: str, variables: Mapping[str, object]) -> dict[str, Any]:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps({"query": document, "variables": variables}).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=30) as answer:
                assert answer.status == 200, answer.status
                return dict(json.load(answer)["data"])

        # The document's own issue, read over the API as any GitHub client reads one.
        design = on_github(
            "query($id:ID!){node(id:$id){... on Issue{id body"
            " boards:projectItems(first:1){nodes{id}}}}}",
            {
                "id": plan_store.qualified(
                    design_approval.design_document(reviewing.home).qualified_id
                )[1]
            },
        )["node"]

        def edited_on_the_board() -> None:
            # A person's edit on GitHub, one `updateIssue` of the body, as its web view sends.
            on_github(
                "mutation($input:UpdateIssueInput!){updateIssue(input:$input){issue{id}}}",
                {"input": {"id": design["id"], "body": design["body"] + "\n"}},
            )

        # The guarded write's own read of the document, which the undo's read of it precedes:
        # the edit lands between the version the undo took and the write that names it.
        # llmlint: ignore-block[tests_mirror_real_usage] A person's edit between two of the undo's
        # own requests has no user-facing timing; the double's request hook is how a race is placed.
        _GitHubFixture.before_operation[_Operation.DOCUMENT_IN_PROJECT] = edited_on_the_board
        refused = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        _GitHubFixture.before_operation.pop(_Operation.DOCUMENT_IN_PROJECT, None)
        # llmlint: ignore-end[tests_mirror_real_usage]
        recorded = _recorded(reviewing.home)
        still = _one_review(reviewing, "completed")
        retried = _recipe(reviewing.environment, "human-items", "reopen", str(item["id"]))
        removed = _recorded(reviewing.home)

    assert refused.returncode != 0, refused.stdout + refused.stderr
    said = " ".join(refused.stderr.split())
    assert "changed after this undo read it, so its approval stands" in said, said
    assert design_approval.VERSION_MOVED in said, said
    assert recorded == item["approval"]["key"]
    assert still["id"] == item["id"], still
    assert retried.returncode == 0, retried.stdout + retried.stderr
    assert removed is None
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
    undo = effective["approval_adapters"]["design-approval"]["undo"]
    assert undo[-2:] == ["orchestrator-design-review", "undo"], undo
    assert effective["enabled"] is True and effective["task_source"] == "human-public", effective
