"""Public copies inventory private addresses before writing, through the real recipes.

Only hosted APIs and paid words are doubled. A private tracker that answers lets the
copy land without private addresses; one that refuses stops the copy before any write.
Private task and unclassified repository refusals remain independent journeys.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


class _Tracker(BaseHTTPRequestHandler):
    """An empty private tracker, or an immediate service failure, recorded on the wire."""

    requests: list[str] = []
    available = True

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback
        sent = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        self.requests.append(sent)
        query = json.loads(sent)["query"]
        empty = {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        if self.available and "mutation" not in query:
            body = {"data": {"issues": empty, "projects": empty, "documents": empty}}
            self.send_response(200)
        else:
            body = {"errors": [{"message": "private tracker unavailable"}]}
            self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _tracker(available: bool) -> Iterator[str]:
    _Tracker.requests = []
    _Tracker.available = available
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Tracker)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/graphql"
    finally:
        server.shutdown()
        serving.join(timeout=5)
        server.server_close()


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
@pytest.mark.parametrize("available", [True, False], ids=["answering", "unavailable"])
def test_a_public_copy_checks_private_addresses_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    """A complete private inventory admits a clean copy; a failed one writes nothing."""
    boundary_home.answering(monkeypatch)
    environment, located, _ = _drafted_for_the_board(tmp_path, monkeypatch, approve=False)
    with _tracker(available) as endpoint, _serving_board() as remote:
        layered = {**environment, **remote, **_organization_layer(tmp_path, endpoint)}
        copied = _recipe(layered, "copy-plan", RENDERED_QUALIFIED, "--to", "plans")
        landed = list(BOARD.issues_created_and_kept())
        mutations = _mutations()
    assert _Tracker.requests, "the private inventory was never checked"
    if available:
        assert copied.returncode == 0, copied.stdout + copied.stderr
        assert len(_copied(copied.stdout)) == 1 + 2 + 1, copied.stdout
        assert RENDERED_PROJECT in [issue.title for issue in landed]
        assert RENDERED_FIRST.title in [issue.title for issue in landed]
        for issue in landed:
            assert located not in issue.body, issue.body
            assert "hiddenco" not in issue.body, issue.body
    else:
        assert copied.returncode != 0, copied.stdout + copied.stderr
        assert "private tracker unavailable" in copied.stdout + copied.stderr
        assert mutations == [] and landed == [], (mutations, landed)


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
