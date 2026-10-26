"""A screened board copy asks only what its plan reaches, and refuses before it writes.

The tracked `onetaskgraph.yaml` switches the plan store's public write boundary on, so every
`just copy-plan` onto the public `plans` board is screened through the installed `onevcs`. A
host doing an organization's work declares that organization's trackers private in its user
layer, and the copies of this repository's own plans — which name none of them — must neither
ask those trackers anything nor wait on one that is down: on onetaskgraph 0.11.0 a public-only
copy asked every source declared private which of its tasks recorded the copied ones, and an
unreachable tracker failed the copy after its writes had landed.

What a copy carries that the board may not hold is the other half: a task declared private,
or one naming a repository whose visibility nobody can answer, is refused before any write
reaches the board, so nothing public is created and then deleted.

Only the hosted services are doubled: the GitHub board is `tests/github_board.py`'s loopback
double, and the organization's Linear tracker a loopback port that takes every connection and
answers none. The recipe, the installed plan store and `onevcs`, and this host's templates are
real, and `onevcs` answers each repository's visibility from a registry the test writes
through it (`tests/boundary_home.py`).
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import boundary_home
import onetaskgraph_user_layer
import pytest
from github_board import BOARD, _GitHubFixture, _serving_board
from project_fixtures import reviewed
from test_board_copy_design_approval_e2e import (
    RENDERED_ANSWERS,
    RENDERED_FIRST,
    RENDERED_PROJECT,
    RENDERED_QUALIFIED,
    _copied,
    _drafted_for_the_board,
    _recipe,
    _through_the_template,
)


class _Held:
    """A hosted service held unavailable: every connection is taken, and none is answered."""

    def __init__(self) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.connections: list[socket.socket] = []
        self.stopped = threading.Event()

    @property
    def endpoint(self) -> str:
        host, port = self.listener.getsockname()[:2]
        return f"http://{host}:{port}/graphql"

    def serve(self) -> None:
        self.listener.settimeout(0.2)
        while not self.stopped.is_set():
            try:
                taken, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.connections.append(taken)


@contextmanager
def _held_service() -> Iterator[_Held]:
    held = _Held()
    serving = threading.Thread(target=held.serve, daemon=True)
    serving.start()
    try:
        yield held
    finally:
        held.stopped.set()
        serving.join(timeout=5)
        for taken in held.connections:
            taken.close()
        held.listener.close()


def _organization_layer(tmp_path: Path, endpoint: str) -> dict[str, str]:
    """The example user layer, its organization's Linear trackers served at ``endpoint``.

    Both are declared private, as a host doing that organization's work declares them, and
    their credential is set, so nothing but the endpoint stands between a request and them.
    The host's own private boards are left out: their endpoint is the live board.
    """
    layer = onetaskgraph_user_layer.without_private_stores()
    for name in (
        onetaskgraph_user_layer.ORGANIZATION_PLANS,
        onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
    ):
        layer["sources"][name]["config"]["endpoint"] = endpoint
    home = onetaskgraph_user_layer.installed(tmp_path / "config-home", layer)
    return {
        onetaskgraph_user_layer.XDG_CONFIG_HOME: str(home),
        onetaskgraph_user_layer.LINEAR_KEY_ENV: "fixture-linear-key",
    }


def _with_a_third_task(
    tmp_path: Path, environment: dict[str, str], *flags: str
) -> subprocess.CompletedProcess[str]:
    """Add one more task to the plan through the template, as a planner adds one."""
    answered = tmp_path / "third.json"
    answered.write_text(json.dumps(RENDERED_ANSWERS[RENDERED_FIRST.node]), encoding="utf-8")
    return _through_the_template(
        environment,
        "create",
        "authoring",
        "--template-loader",
        "-",
        "--no-interactive",
        "--project",
        RENDERED_PROJECT,
        "--title",
        "feat: export the listing",
        "--answers",
        str(answered),
        '--metadata=onepipeline.id="export-the-listing"',
        '--metadata=onepipeline.persona="engineer"',
        '--metadata=orchestrator.unit="Listing"',
        *flags,
    )


def _mutations() -> list[str]:
    """Every write the board double took, by operation."""
    return [str(request.operation) for request in _GitHubFixture.requests if request.is_mutation]


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `board-copy-approval` is
# this directory's own leaf project, keyed on `boardCopyApprovalWorkspace`, the edge this rule
# asks for; that key names the recipes, scripts and package these journeys drive.
# llmlint: ignore-block[e2e_not_mocked] Only the hosted services are doubled — the GitHub board,
# because driving it writes to the live board this repository plans on, and the organization's
# Linear tracker, because no check may reach a Linear workspace — and the paid model `reviewed`
# scripts; the recipe, the installed plan store and onevcs, and the records they write are real.
def test_a_public_only_copy_neither_asks_nor_waits_on_an_unrelated_private_tracker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy lands whole while the organization's private tracker takes connections, unanswered.

    Not one connection reaches it: the plan names no repository routed there and no item of
    it, so screening the copy has nothing to ask it, and a request made anyway would hold the
    copy until the store gave up.
    """
    boundary_home.answering(monkeypatch)
    environment, _, _ = _drafted_for_the_board(tmp_path, monkeypatch, approve=False)
    with _held_service() as tracker, _serving_board() as remote:
        layered = {**environment, **remote, **_organization_layer(tmp_path, tracker.endpoint)}
        copied = _recipe(layered, "copy-plan", RENDERED_QUALIFIED, "--to", "plans")
        landed = sorted(issue.title for issue in BOARD.issues_created_and_kept())
        contacted = len(tracker.connections)
        # The tracker was listening and counting all along: a connection made now is taken.
        host, port = tracker.listener.getsockname()[:2]
        with socket.create_connection((host, port), timeout=5):
            deadline = time.monotonic() + 5
            while len(tracker.connections) == contacted and time.monotonic() < deadline:
                time.sleep(0.05)
        counted = len(tracker.connections) - contacted

    assert copied.returncode == 0, copied.stdout + copied.stderr
    assert counted == 1, "the held tracker took no connection, so it could not have seen one"
    assert contacted == 0, f"the copy reached the private tracker {contacted} time(s)"
    assert len(_copied(copied.stdout)) == 1 + 2 + 1, copied.stdout
    assert RENDERED_PROJECT in landed and RENDERED_FIRST.title in landed, landed


def _declared_private(root: Path) -> None:
    """Declare the plan's project private in its record, as a planner whose plan is private does.

    The store holds a public project to never holding a private member, so a task declared
    private, or naming a repository nobody can say is public, sits under a private project.
    """
    record = root / "authoring" / "projects" / f"{RENDERED_PROJECT}.md"
    text = record.read_text(encoding="utf-8")
    # llmlint: ignore[tests_mirror_real_usage] The project record is the hand-written one a planner authors (`personas/planner.yaml`), and the store's own create writes a private project's classification as exactly this frontmatter key.  # noqa: E501
    record.write_text(
        text.replace('status: "todo"\n', 'status: "todo"\nclassification: private\n', 1)
    )


def _refused_writing_nothing(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """`just copy-plan` of the plan onto the board, held to sending the board no write."""
    with _serving_board() as remote:
        copied = _recipe(
            {**environment, **remote}, "copy-plan", RENDERED_QUALIFIED, "--to", "plans"
        )
        written = _mutations()
        created = list(BOARD.created)
    assert copied.returncode != 0, copied.stdout + copied.stderr
    assert written == [] and created == [], (written, created)
    return copied


def test_a_task_declared_private_is_refused_before_anything_reaches_the_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan carrying a task declared private is refused, and the board takes no write."""
    boundary_home.answering(monkeypatch)
    environment, _, _ = _drafted_for_the_board(tmp_path, monkeypatch, approve=False)
    _declared_private(tmp_path)
    added = _with_a_third_task(tmp_path, environment, "--classification", "private")
    assert added.returncode == 0, added.stdout + added.stderr
    reviewed(RENDERED_QUALIFIED)

    copied = _refused_writing_nothing(environment)
    assert "private" in copied.stdout + copied.stderr, copied.stdout + copied.stderr


#: A repository no `onevcs` registration names, so who may read it cannot be answered.
UNREGISTERED = "github.com/nowhere-known/unregistered-service"


def test_a_task_naming_a_repository_nobody_can_classify_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task naming a repository `onevcs` holds no visibility for is refused, writing nothing.

    `onevcs boundary inspect` answers such a repository `unknown`, which the boundary reads as
    private: the reference cannot be resolved to a public one, so it never reaches the board.
    Creating the task under the plan's public project is refused already, so the plan is
    declared private as its own record can be, and the copy onto the public board refused.
    """
    boundary_home.answering(monkeypatch)
    environment, _, _ = _drafted_for_the_board(tmp_path, monkeypatch, approve=False)
    under_public = _with_a_third_task(tmp_path, environment, f"--repository={UNREGISTERED}")
    assert under_public.returncode != 0, under_public.stdout + under_public.stderr
    _declared_private(tmp_path)
    added = _with_a_third_task(tmp_path, environment, f"--repository={UNREGISTERED}")
    assert added.returncode == 0, added.stdout + added.stderr
    reviewed(RENDERED_QUALIFIED)

    copied = _refused_writing_nothing(environment)
    assert "private" in copied.stdout + copied.stderr, copied.stdout + copied.stderr
    # llmlint: ignore-end[e2e_not_mocked]
    # llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
