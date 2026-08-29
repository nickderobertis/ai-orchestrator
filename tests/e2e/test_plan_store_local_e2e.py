"""The local plan store this repository plans against, driven through the installed CLI.

This repository plans under the `plans-local` source rather than on the `plans` GitHub
Projects board — a temporary retreat recorded in `AGENTS.md`, *Where a plan of this
repository lives*. Three properties follow from that and are proven here against the real
`onetaskgraph.yaml` and the real pinned binary: a plan stored under `plans-local` is
readable, a project stored there is launchable by its qualified id, and the `plans` source
is still configured with every value it carried before.

**Configured is the property, not reachable.** Whether the board answers is outside this
suite: it needs a credential a checkout need not have, and it is the store the retreat
exists to stop trusting. So the board's endpoint is pointed at a recording server that
answers nothing, and the assertion is that **no request ever reached it**.

The fixture holds two projects and two tasks on purpose. Every project's title differs
from its own identifier, because under `local-md` a project's identifier is its document's
file stem: a fixture whose stem and title are one word cannot tell a store that answered
with the title from one that answered with the identifier, and that blindness is how a
settlement projection that renames every destination project to its native identifier
reached a release. And every scoped read here is a predicate two items straddle, so an
ignored filter and an honoured one cannot produce the same answer — which is the other
defect behind the retreat.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from fake_backend import PROMPT_LOG_ENV
from test_orchestrate_launch_e2e import _environment as _launch_environment

from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

#: The source this repository's own plans live under, and the board it retreated from.
LOCAL_SOURCE = "plans-local"
BOARD_SOURCE = "plans"
#: The `plans` source's every configured value, as it stood before the retreat. Restated
#: here deliberately: this is the record of what "unchanged" means, so a value edited in
#: `onetaskgraph.yaml` fails against this list rather than against a copy of itself.
BOARD_SETTINGS: dict[str, object] = {
    "sources.plans.plugin": "github-projects",
    "sources.plans.config.owner": "nickderobertis",
    "sources.plans.config.project_number": 2,
    "sources.plans.config.repository": "nickderobertis/ai-orchestrator",
    "sources.plans.config.token_env": "GH_PROJECTS_TOKEN",
}


@dataclass(frozen=True)
class _StoredProject:
    """One project of the fixture, in the two halves a store can confuse."""

    native_id: str
    title: str
    task_id: str
    task_title: str


#: Two projects, each titled unlike its own identifier, each holding one task nothing
#: else holds. `alpha`/`beta` are the identifiers; the titles share no word with them.
FIXTURE = (
    _StoredProject(
        native_id="alpha",
        title="First tracked workstream",
        task_id="opening",
        task_title="test: the first workstream's only task",
    ),
    _StoredProject(
        native_id="beta",
        title="Second tracked workstream",
        task_id="closing",
        task_title="test: the second workstream's only task",
    ),
)


class _SilentBoard(BaseHTTPRequestHandler):
    """A board endpoint that records every request and answers none of them usefully."""

    requests: ClassVar[list[str]] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        _SilentBoard.requests.append(self.path)
        self.send_response(500)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        self.do_POST()

    def log_message(self, format: str, *args: object) -> None:
        """Keep the fixture out of pytest's captured output."""


@contextmanager
def _watched_board() -> Iterator[dict[str, str]]:
    """Point the `plans` source at a recorder, yielding the environment that does it."""
    _SilentBoard.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SilentBoard)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield {
            "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": (
                f"http://127.0.0.1:{server.server_port}/graphql"
            )
        }
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _write_fixture(root: Path) -> None:
    for project in FIXTURE:
        write_plan_project(
            root,
            {
                "schema_version": 3,
                "name": project.title,
                "tasks": [
                    {
                        "id": project.task_id,
                        "persona": "engineer",
                        "title": project.task_title,
                        "task": "## What\nReply with done.\n\n## Why\nProve the store "
                        "reads.\n\n## Acceptance criteria\n- The task settles.\n",
                    }
                ],
            },
            native_id=project.native_id,
        )


def _store_environment(root: Path | None, board: dict[str, str]) -> dict[str, str]:
    """The environment a store read runs under, with the local root pointed per run."""
    environment = os.environ.copy()
    environment.update(board)
    environment["ONETASKGRAPH_SECRETS_FILE"] = str(REPO_ROOT / "does-not-exist.env")
    environment.pop("GH_PROJECTS_TOKEN", None)
    if root is not None:
        environment[f"ONETASKGRAPH_SOURCES__{LOCAL_SOURCE.upper()}__CONFIG__ROOT"] = str(root)
    return environment


def _isolated_local_roots(tmp_path: Path, board: dict[str, str]) -> dict[str, str]:
    """A store environment whose other `local-md` sources answer with nothing.

    An unqualified read asks every default source, so a journey that left `authoring`
    and `examples` pointed at this checkout would be asserting on whatever a developer
    happened to have authored locally. Each is given an empty root of its own — never a
    shared one, which is the duplicate-listing failure these roots are kept apart to
    avoid.
    """
    environment = _store_environment(tmp_path / LOCAL_SOURCE, board)
    _write_fixture(tmp_path / LOCAL_SOURCE)
    for source in ("AUTHORING", "EXAMPLES", "TEST_FIXTURES"):
        empty = tmp_path / source.lower()
        (empty / "projects").mkdir(parents=True, exist_ok=True)
        (empty / "tasks").mkdir(parents=True, exist_ok=True)
        environment[f"ONETASKGRAPH_SOURCES__{source}__CONFIG__ROOT"] = str(empty)
    return environment


def _plans(root: Path | None, *arguments: str, board: dict[str, str]) -> object:
    """Run `just plans` against the committed configuration and parse its JSON answer.

    Typed `object` rather than a payload shape: the CLI answers a different document per
    verb, and the readers below narrow whichever one they were handed at the point they
    read it.
    """
    result = subprocess.run(
        ["just", "plans", "--json", *arguments],
        cwd=REPO_ROOT,
        env=_store_environment(root, board),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"{arguments}: {result.stderr}"
    parsed: object = json.loads(result.stdout)
    return parsed


def _titles(payload: object) -> dict[str, str]:
    """Every row of a store answer, as `{qualified id: title}`."""
    assert isinstance(payload, dict), payload
    assert payload["errors"] == [], payload["errors"]
    rows = payload["items"]
    assert isinstance(rows, list), rows
    titles: dict[str, str] = {}
    for row in rows:
        assert isinstance(row, dict), row
        identifier, item = row["id"], row["item"]
        assert isinstance(identifier, str) and isinstance(item, dict), row
        title = item["title"]
        assert isinstance(title, str), item
        titles[identifier] = title
    return titles


def _sources_asked(payload: object) -> list[str]:
    """Which sources the answer says contributed to it."""
    assert isinstance(payload, dict), payload
    plan = payload["plan"]
    assert isinstance(plan, dict), plan
    per_source = plan["per_source"]
    assert isinstance(per_source, list), per_source
    asked: list[str] = []
    for entry in per_source:
        assert isinstance(entry, dict), entry
        name = entry["source"]
        assert isinstance(name, str), entry
        asked.append(name)
    return asked


def _resolved_settings(payload: object) -> dict[str, object]:
    """Every setting `config show` resolved, as `{dotted key: value}`."""
    assert isinstance(payload, dict), payload
    settings = payload["settings"]
    assert isinstance(settings, list), settings
    resolved: dict[str, object] = {}
    for entry in settings:
        assert isinstance(entry, dict), entry
        key = entry["key"]
        assert isinstance(key, str), entry
        resolved[key] = entry["value"]
    return resolved


def test_the_fixture_can_tell_a_title_from_an_identifier() -> None:
    """Every fixture project is titled unlike its own file stem, or it proves nothing.

    Asserted rather than assumed: a fixture that lost this property would leave every
    read below passing against a store answering with the wrong field, which is the
    defect that put a renaming write-back into a release.
    """
    assert len(FIXTURE) >= 2, "a scoped read needs a second project to be scoped away from"
    for project in FIXTURE:
        assert project.title != project.native_id, project
        assert project.native_id not in project.title.lower(), (
            f"{project.native_id!r} appears inside its own title, so a store answering "
            "with the identifier would still look right"
        )


def test_a_project_stored_under_the_local_source_is_readable(tmp_path: Path) -> None:
    """`just plans` reads both stored projects back, by their authored titles."""
    _write_fixture(tmp_path)
    with _watched_board() as board:
        listed = _plans(tmp_path, "project", "list", "--source", LOCAL_SOURCE, board=board)
        shown = _plans(
            tmp_path, "project", "show", f"{LOCAL_SOURCE}:{FIXTURE[0].native_id}", board=board
        )

    assert _titles(listed) == {
        f"{LOCAL_SOURCE}:{project.native_id}": project.title for project in FIXTURE
    }
    assert _sources_asked(listed) == [LOCAL_SOURCE]
    assert _titles(shown) == {f"{LOCAL_SOURCE}:{FIXTURE[0].native_id}": FIXTURE[0].title}, (
        "a qualified show answers with the authored title; answering with the file stem "
        "is the renaming this store was retreated to in order to avoid"
    )


def test_a_read_scoped_to_one_stored_project_answers_with_that_project_alone(
    tmp_path: Path,
) -> None:
    """The scoped read separates the two projects, which an ignored filter cannot.

    This is the defect the retreat is *from*, asserted against the store the retreat is
    *to*: the unscoped read is taken in the same journey, so a filter that did nothing
    would return the same two rows twice rather than one row and two.
    """
    _write_fixture(tmp_path)
    scoped_to, held_back = FIXTURE
    with _watched_board() as board:
        every = _plans(tmp_path, "task", "list", "--source", LOCAL_SOURCE, board=board)
        scoped = _plans(
            tmp_path,
            "task",
            "list",
            "--project",
            f"{LOCAL_SOURCE}:{scoped_to.native_id}",
            board=board,
        )

    assert set(_titles(every).values()) == {project.task_title for project in FIXTURE}, (
        "the unscoped read has to return both projects' tasks, or the scoped read below "
        "is narrowing nothing"
    )
    assert set(_titles(scoped).values()) == {scoped_to.task_title}
    assert held_back.task_title not in set(_titles(scoped).values())


def test_a_project_stored_under_the_local_source_is_launchable(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`just orchestrate plans-local:<project>` runs the plan stored there.

    The documents tell a reader to launch this repository's plans by that qualified id,
    so the id is launched here rather than only read: a source a `project show` answers
    and `onepipeline start` cannot load would leave that instruction pointing at nothing.
    Only the paid provider is substituted; the plan store, the recipe, and the engine are
    the real ones.
    """
    store = tmp_path / "store"
    store.mkdir()
    _write_fixture(store)
    launched_project = FIXTURE[0]
    with _watched_board() as board:
        environment = _launch_environment(tmp_path / "execution", oneharness_bin)
        environment.update(_store_environment(store, board))
        environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")
        launched = subprocess.run(
            [
                "just",
                "orchestrate",
                f"{LOCAL_SOURCE}:{launched_project.native_id}",
                "--dag-graph",
                "off",
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert launched.returncode == 0, launched.stdout + launched.stderr
        settled = _plans(
            store,
            "task",
            "list",
            "--project",
            f"{LOCAL_SOURCE}:{launched_project.native_id}",
            board=board,
        )
        reached = list(_SilentBoard.requests)

    assert (tmp_path / "turns.jsonl").is_file(), "the stored plan reached no execution turn"
    assert set(_titles(settled).values()) == {launched_project.task_title}, (
        "the settlement write-back has to leave the launched project's task titled as it "
        "was authored"
    )
    assert reached == [], f"launching a local plan reached the plan board at {reached}"


def test_a_locally_stored_plan_answers_a_read_that_names_no_source(tmp_path: Path) -> None:
    """An unqualified listing returns the `plans-local` projects, exactly once each.

    Naming `plans-local` in `default_sources` is what makes a read that names no source
    answer with the plans stored there, so it is driven rather than read out of the file:
    a source configured but left out of that list is one every default read silently
    omits. `--allow-partial` because the board carries no credential here and is expected
    to contribute nothing — its refusal is asserted rather than skipped over.

    Once each is the other half. Two `local-md` sources over one root would answer with
    the same project under both names, which is not an error anywhere and shows up only
    as a duplicated row.
    """
    with _watched_board() as board:
        result = subprocess.run(
            ["just", "plans", "--json", "project", "list", "--allow-partial"],
            cwd=REPO_ROOT,
            env=_isolated_local_roots(tmp_path, board),
            text=True,
            capture_output=True,
            check=False,
        )
        reached = list(_SilentBoard.requests)

    assert result.returncode == 0, result.stderr
    payload: object = json.loads(result.stdout)
    assert isinstance(payload, dict), payload
    rows = payload["items"]
    assert isinstance(rows, list), rows
    identifiers = [row["id"] for row in rows if isinstance(row, dict)]
    expected = [f"{LOCAL_SOURCE}:{project.native_id}" for project in FIXTURE]
    assert sorted(identifiers) == sorted(expected), (
        f"a read naming no source answered {identifiers}; it has to return every "
        f"{LOCAL_SOURCE} project once, and nothing else once the other local roots are empty"
    )
    refused = payload["errors"]
    assert isinstance(refused, list) and len(refused) == 1, refused
    assert refused[0]["source"] == BOARD_SOURCE, refused
    assert reached == [], f"an unqualified read reached the plan board at {reached}"


def test_the_board_source_is_still_configured_with_every_value_it_carried() -> None:
    """`plans` keeps its plugin, owner, project number, repository and credential name.

    Read through the CLI's own resolved configuration rather than out of the file, so
    what is asserted is the configuration the store would actually use.
    """
    with _watched_board() as board:
        resolved = _resolved_settings(_plans(None, "config", "show", board=board))

    for key, expected in BOARD_SETTINGS.items():
        assert resolved.get(key) == expected, (
            f"the `{BOARD_SOURCE}` source resolves {key} to {resolved.get(key)!r} where it "
            f"carried {expected!r}; retiring the retreat deletes `{LOCAL_SOURCE}` and "
            "leaves this source exactly as it is"
        )
    assert resolved[f"sources.{LOCAL_SOURCE}.plugin"] == "local-md"
    defaults = resolved["default_sources"]
    assert isinstance(defaults, list) and LOCAL_SOURCE in defaults, defaults


@pytest.mark.parametrize("journey", ["read", "configuration"])
def test_no_read_of_the_local_store_reaches_the_board(tmp_path: Path, journey: str) -> None:
    """Nothing this journey runs makes a request to the plan board.

    The endpoint is a live recorder rather than an unroutable address, so a request that
    was made is caught rather than merely failing: proving the board is *configured* is
    this suite's business, and reaching it is not.
    """
    _write_fixture(tmp_path)
    with _watched_board() as board:
        if journey == "read":
            _plans(tmp_path, "project", "list", "--source", LOCAL_SOURCE, board=board)
            _plans(
                tmp_path,
                "task",
                "list",
                "--project",
                f"{LOCAL_SOURCE}:{FIXTURE[0].native_id}",
                board=board,
            )
        else:
            _plans(None, "config", "show", board=board)
        reached = list(_SilentBoard.requests)

    assert reached == [], (
        f"the {journey} journey reached the plan board at {reached}; this suite proves the "
        f"`{BOARD_SOURCE}` source is configured, never that it is reachable"
    )
