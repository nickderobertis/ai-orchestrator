"""Drive this checkout's committed plan-store configuration through the real CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, Literal, NewType, TypedDict

from fake_backend import PROMPT_LOG_ENV
from stub_onetaskgraph import LOG_ENV, PASS_SHOWS_ENV, REAL_ENV, STUBBED
from test_orchestrate_launch_e2e import _environment as _launch_environment

from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

ADOPTED = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
#: The fault injector this journey points `onepipeline` at. Its marker and its three
#: steering variables are imported from that module rather than restated, so the log
#: these assertions read cannot drift from the log that file writes.
STUB_ONETASKGRAPH = Path(__file__).resolve().parent / "stub_onetaskgraph.py"
_NodeId = NewType("_NodeId", str)
_PipelineId = NewType("_PipelineId", str)
#: A project's native id inside one source — the `<native>` half of a qualified
#: `<source>:<native>`, and what a stored project answers as its own `id`.
_ProjectId = NewType("_ProjectId", str)
#: The project `_write_local_project` renders, named once so the journeys that read it
#: back and the helper that authors its description agree on one identifier.
LOCAL_PROJECT = _ProjectId("launch")
#: The same project as every recipe here names it: qualified by the `authoring` source
#: `onetaskgraph.yaml` roots at this checkout's `.plans`, which these journeys point
#: elsewhere per run. Derived rather than restated, so the native id has one source.
LOCAL_QUALIFIED = f"authoring:{LOCAL_PROJECT}"


class _AddedNode(TypedDict):
    id: _NodeId
    task: str
    expects_no_diff: bool


class _AddCommand(TypedDict):
    op: Literal["add"]
    node: _AddedNode


class _LiveEditEnvelope(TypedDict):
    version: Literal[1]
    commands: list[_AddCommand]


@dataclass(frozen=True)
class _Settlement:
    status: str
    outcome: str | None

    @classmethod
    def from_mapping(cls, value: object) -> _Settlement:
        if not isinstance(value, dict):
            raise ValueError("stored task requires a settlement object")
        status = value.get("status")
        outcome = value.get("outcome")
        if not isinstance(status, str) or (outcome is not None and not isinstance(outcome, str)):
            raise ValueError("stored task settlement requires string status and outcome values")
        return cls(status=status, outcome=outcome)


@dataclass(frozen=True)
class _StoredTask:
    pipeline_id: _PipelineId
    settlement: _Settlement

    @classmethod
    def from_item(cls, value: object) -> _StoredTask:
        if not isinstance(value, dict):
            raise ValueError("stored task list item must be an object")
        item = value.get("item")
        if not isinstance(item, dict):
            raise ValueError("stored task list item requires an item object")
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("stored task requires a metadata object")
        pipeline_id = metadata.get("onepipeline.id")
        if not isinstance(pipeline_id, str):
            raise ValueError("stored task requires a pipeline id and settlement object")
        settlement = _Settlement.from_mapping(metadata.get("onepipeline.settlement"))
        return cls(pipeline_id=_PipelineId(pipeline_id), settlement=settlement)


def _stored_tasks(body: str) -> list[_StoredTask]:
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("stored task response requires an items array")
    return [_StoredTask.from_item(item) for item in payload["items"]]


def _stored_project_content(body: str, project_id: _ProjectId) -> str | None:
    """The description one stored project carries, read through the store's own surface.

    Read as the reader rather than off the local-md file, because the property this
    journey is about is what a *consumer* of the plan store sees — an operator opening
    the board, or `just plans` — and the write-back reaches that through the store's
    own contract rather than by editing the file this checkout happened to render.

    A project carrying no description is returned as `None` rather than refused,
    because that is precisely the state the deletion this journey is about leaves
    behind: refusing it here would fail the journey inside its reader, where the
    message is about a response shape, instead of at the comparison that can say the
    description was rewritten.
    """
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("stored project response requires an items array")
    for entry in payload["items"]:
        if not isinstance(entry, dict):
            raise ValueError("stored project list item must be an object")
        item = entry.get("item")
        if not isinstance(item, dict):
            raise ValueError("stored project list item requires an item object")
        if item.get("id") != project_id:
            continue
        content = item.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError(f"stored project {project_id!r} carries a non-string content")
        return content
    raise ValueError(f"the store holds no project {project_id!r}")


@dataclass(frozen=True)
class _GitHubProjectFixture:
    node_id: str = "PVT_fixture"
    title: str = "AI Orchestrator"
    description: str = "Permanent plans"
    url: str = "https://github.com/users/nickderobertis/projects/2"
    timestamp: str = "2026-08-26T00:00:00Z"

    def graphql_response(self) -> dict[str, object]:
        project: dict[str, object] = {
            "id": self.node_id,
            "title": self.title,
            "shortDescription": self.description,
            "url": self.url,
            "createdAt": self.timestamp,
            "updatedAt": self.timestamp,
            "closed": False,
            "fields": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            "items": {
                "nodes": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
        return {
            "data": {
                "owner": {"projectV2": project},
                "user": {"projectV2": None},
            }
        }


@dataclass(frozen=True)
class _GraphQLVariables:
    owner: str
    number: int

    @classmethod
    def from_mapping(cls, value: object) -> _GraphQLVariables:
        if not isinstance(value, dict):
            raise ValueError("GraphQL variables must be an object")
        owner = value.get("owner")
        number = value.get("number")
        if not isinstance(owner, str) or not isinstance(number, int):
            raise ValueError("GraphQL variables require string owner and integer number")
        return cls(owner=owner, number=number)


@dataclass(frozen=True)
class _GraphQLRequest:
    query: str
    variables: _GraphQLVariables

    @classmethod
    def from_json(cls, body: bytes) -> _GraphQLRequest:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("GraphQL request must be an object")
        query = payload.get("query")
        if not isinstance(query, str):
            raise ValueError("GraphQL request requires a query string")
        return cls(query=query, variables=_GraphQLVariables.from_mapping(payload.get("variables")))


FIXTURE = _GitHubProjectFixture()


class _GitHubFixture(BaseHTTPRequestHandler):
    requests: ClassVar[list[_GraphQLRequest]]

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        length = int(self.headers["Content-Length"])
        self.requests.append(_GraphQLRequest.from_json(self.rfile.read(length)))
        body = json.dumps(FIXTURE.graphql_response()).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _plan_environment(root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "ONETASKGRAPH_SECRETS_FILE": str(root / "no-secrets.env"),
            "ONETASKGRAPH_SOURCES__AUTHORING__CONFIG__ROOT": str(root),
        }
    )
    return environment


def _write_local_project(root: Path) -> None:
    write_plan_project(
        root,
        {
            "schema_version": 3,
            "name": LOCAL_PROJECT,
            "tasks": [
                {
                    "id": "probe",
                    "persona": "engineer",
                    "title": "test: launch local project",
                    "task": "## What\nReply with done.\n\n## Why\nProve launch.\n\n"
                    "## Acceptance criteria\n- The task settles.\n",
                }
            ],
        },
    )


def test_credentialed_plan_store_reads_local_and_remote_sources(tmp_path: Path) -> None:
    """The committed owner/project pair reaches a fixture through the installed release."""
    _write_local_project(tmp_path)
    _GitHubFixture.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _GitHubFixture)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        environment = _plan_environment(tmp_path)
        environment.update(
            {
                "GH_PROJECTS_TOKEN": "fixture-token",
                "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": (
                    f"http://127.0.0.1:{server.server_port}/graphql"
                ),
            }
        )
        result = subprocess.run(
            ["just", "plans", "project", "list", "--json"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert {
        "launch",
        "AI Orchestrator",
    }.issubset({item["item"]["title"] for item in payload["items"]})
    assert _GitHubFixture.requests
    variables = _GitHubFixture.requests[0].variables
    assert variables.owner == "nickderobertis"
    assert variables.number == 2


def test_missing_remote_credential_keeps_local_plan_launchable(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A missing remote token costs `plans`, not a local project's execution."""
    _write_local_project(tmp_path)
    environment = _plan_environment(tmp_path)
    environment.pop("GH_PROJECTS_TOKEN", None)
    result = subprocess.run(
        ["just", "plans", "project", "list", "--allow-partial", "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "launch" in [item["item"]["title"] for item in payload["items"]]
    assert len(payload["errors"]) == 1
    error = payload["errors"][0]
    assert error["source"] == "plans"
    assert error["error"]["kind"] == "auth"
    assert "GH_PROJECTS_TOKEN" in error["error"]["message"]

    launched_environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    launched_environment.update(
        {
            "ONETASKGRAPH_SECRETS_FILE": environment["ONETASKGRAPH_SECRETS_FILE"],
            "ONETASKGRAPH_SOURCES__AUTHORING__CONFIG__ROOT": str(tmp_path),
            PROMPT_LOG_ENV: str(tmp_path / "turns.jsonl"),
        }
    )
    launched_environment.pop("GH_PROJECTS_TOKEN", None)
    launched = subprocess.run(
        [
            "just",
            "orchestrate",
            LOCAL_QUALIFIED,
            "--dag-graph",
            "off",
        ],
        cwd=REPO_ROOT,
        env=launched_environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert (tmp_path / "turns.jsonl").is_file(), "the stored plan reached no execution turn"


def test_run_settlements_and_live_edits_reach_the_plan_store(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The real launch writes both execution outcome and a graph addition to its source."""
    _write_local_project(tmp_path)
    environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    environment.update(_plan_environment(tmp_path))
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")

    launched = subprocess.run(
        ["just", "orchestrate", LOCAL_QUALIFIED, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr

    live_edit: _LiveEditEnvelope = {
        "version": 1,
        "commands": [
            {
                "op": "add",
                "node": {
                    "id": _NodeId("record-follow-up"),
                    "task": "Record this follow-up without dispatching.",
                    "expects_no_diff": True,
                },
            }
        ],
    }
    edited = subprocess.run(
        ["just", "channel-reply", "launch"],
        cwd=REPO_ROOT,
        env=environment,
        input=json.dumps(live_edit),
        text=True,
        capture_output=True,
        check=False,
    )
    assert edited.returncode == 0, edited.stdout + edited.stderr

    adopted = subprocess.run(
        ["just", "orchestrate", "--adopt", "launch"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert adopted.returncode == 0, adopted.stdout + adopted.stderr

    stored = subprocess.run(
        ["just", "plans", "task", "list", "--project", LOCAL_QUALIFIED, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stored.returncode == 0, stored.stdout + stored.stderr
    by_pipeline_id = {task.pipeline_id: task for task in _stored_tasks(stored.stdout)}
    assert set(by_pipeline_id) == {"probe", "record-follow-up"}
    assert by_pipeline_id["probe"].settlement == _Settlement(status="done", outcome=None)
    added_settlement = by_pipeline_id["record-follow-up"].settlement
    assert added_settlement.status == "done"
    assert added_settlement.outcome == "no-changes"


#: What a person writes on a board and nothing in a run has any business touching.
#: Deliberately several lines with its own shape, so a comparison that survives it
#: is comparing content rather than emptiness.
AUTHORED_PROJECT_BODY = """Why this board exists, in the operator's own words.

Read the constraint before launching anything: the second dispatch may not start
until the first has landed, and nothing here is safe to re-run twice.
"""


def _author_project_body(root: Path, project: _ProjectId, body: str) -> None:
    """Replace one stored project's description with prose a person wrote.

    Written onto the record rather than through a recipe because this repository has
    no verb that edits a project body — the board is where an operator writes one —
    and what the journey needs is only that the store holds it before the run.
    """
    record = root / "projects" / f"{project}.md"
    frontmatter, _, _ = record.read_text(encoding="utf-8").rpartition("---\n")
    record.write_text(f"{frontmatter}---\n\n{body}", encoding="utf-8")


def test_settlement_write_back_preserves_the_authored_project_description(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A settled run leaves the project body its operator wrote exactly as written.

    The projection a settlement writes back does not own the project description, and
    the release below the adopted one nonetheless replaced it with an empty string:
    the write-back built its shadow project with the body hardcoded empty, and the
    copy that followed is a total replace by contract, so every destination faithfully
    propagated the deletion. Task bodies survived, which is what let it ship.

    Asserted as a *comparison* of the content before and against after, never as the
    content being present: an assertion that the description is non-empty, or that the
    project is still readable, cannot fail on a deletion — and that assertion shape is
    exactly how this reached a release. The settlement is asserted beside it, because
    a run whose projection never reached the store would preserve the description by
    doing nothing at all.
    """
    _write_local_project(tmp_path)
    _author_project_body(tmp_path, LOCAL_PROJECT, AUTHORED_PROJECT_BODY)
    environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    environment.update(_plan_environment(tmp_path))
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")

    def _show_project() -> str | None:
        shown = subprocess.run(
            ["just", "plans", "project", "show", LOCAL_QUALIFIED, "--json"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert shown.returncode == 0, shown.stdout + shown.stderr
        return _stored_project_content(shown.stdout, LOCAL_PROJECT)

    before = _show_project()
    assert before == AUTHORED_PROJECT_BODY.strip(), (
        "the journey has to start from a description the store really holds, and this "
        f"one reads {before!r}"
    )

    launched = subprocess.run(
        ["just", "orchestrate", LOCAL_QUALIFIED, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr

    stored = subprocess.run(
        ["just", "plans", "task", "list", "--project", LOCAL_QUALIFIED, "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert stored.returncode == 0, stored.stdout + stored.stderr
    settled = {task.pipeline_id: task.settlement for task in _stored_tasks(stored.stdout)}
    assert settled == {"probe": _Settlement(status="done", outcome=None)}, (
        "the write-back has to have reached this project for its preservation to be "
        f"the thing under test, and the store reports {settled}"
    )

    after = _show_project()
    assert after == before, (
        f"the settlement rewrote the project description its operator authored to "
        f"{after!r}; the write-back owns the node projection and nothing else on that "
        "record"
    )


#: The `project show` invocations that pass through the fault injector untouched. One:
#: the launch's own read of the plan it is about to run, which has to succeed or there
#: is no settlement to measure. Every read after it belongs to the write-back worker.
LAUNCH_PLAN_READS = 1


# llmlint: ignore-block[e2e_not_mocked, tests_mirror_real_usage] A partial read is a
# real condition of the real surface — the sibling journey above asserts one that a
# missing `GH_PROJECTS_TOKEN` produces — but it cannot be arranged *at the write-back's
# own read and not at the launch's*, because the two are the same command against the
# same source and a store broken for real breaks the run being measured. So this
# substitutes the one delegated published CLI at `ONETASKGRAPH_BIN`, the seam
# `onepipeline` spawns it through, and nothing above it: the recipe, the wrapper, the
# engine, its write-back worker and the store on disk all stay real, `just plans` reads
# the result through the installed binary because that recipe never consults this
# variable, and the injector runs the real CLI for every call and adds one `errors`
# entry to the answer it actually returned.
def _injected_partial_read(log: Path) -> dict[str, str]:
    """Point `onepipeline`'s plan-store calls at the fault injector, and log them."""
    real_onetaskgraph = shutil.which("onetaskgraph")
    assert real_onetaskgraph, "the adopted onetaskgraph must be on PATH — run 'just bootstrap'"
    return {
        "ONETASKGRAPH_BIN": str(STUB_ONETASKGRAPH),
        REAL_ENV: real_onetaskgraph,
        LOG_ENV: str(log),
        PASS_SHOWS_ENV: str(LAUNCH_PLAN_READS),
    }


# llmlint: ignore-end[e2e_not_mocked, tests_mirror_real_usage]


def _stub_invocations(log: Path) -> list[str]:
    """Every plan-store call the engine made, in order, as the injector saw them."""
    return log.read_text(encoding="utf-8").splitlines() if log.is_file() else []


def test_a_refused_destination_read_settles_the_run_and_writes_nothing(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """A destination read the store cannot answer stops the projection and nothing else.

    The recovery path beside
    `test_settlement_write_back_preserves_the_authored_project_description`, and the
    half that says why the preservation is worth anything: the write-back reads its
    destination before it builds the shadow it copies over, and a read it cannot trust
    has to end the projection rather than fall back to a default. A read that defaulted
    would be a read that deletes — the shadow would carry an empty description and the
    copy is a total replacement by contract — so this is the same defect arriving
    through the other door.

    Both halves are asserted, because either alone passes for the wrong reason. That
    the record is untouched is not evidence on its own: a run that never projected at
    all leaves exactly that. So the injector's own log is read for what the engine
    *did* — that the launch's plan read went through, that the write-back's read was
    the one refused, and that no `project copy` was ever reached — and the record is
    compared byte for byte beside it.

    Below the refusal this fails three times over, each for its own reason:
    onepipeline 0.16.2 performs no destination read at all, so nothing is injected
    into, `project copy` runs, and the record comes back rewritten with an empty body.
    """
    _write_local_project(tmp_path)
    _author_project_body(tmp_path, LOCAL_PROJECT, AUTHORED_PROJECT_BODY)
    record = tmp_path / "projects" / f"{LOCAL_PROJECT}.md"
    before = record.read_bytes()

    log = tmp_path / "plan-store-calls.log"
    environment = _launch_environment(tmp_path / "execution", oneharness_bin)
    environment.update(_plan_environment(tmp_path))
    environment.update(_injected_partial_read(log))
    environment[PROMPT_LOG_ENV] = str(tmp_path / "turns.jsonl")

    launched = subprocess.run(
        ["just", "orchestrate", LOCAL_QUALIFIED, "--dag-graph", "off"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert json.loads(launched.stdout.splitlines()[-1]) == {
        "run_id": LOCAL_PROJECT,
        "settlement": "complete",
    }, (
        "a store that cannot answer the write-back's read must not decide the run; "
        f"this one settled as {launched.stdout.splitlines()[-1]!r}"
    )

    invocations = _stub_invocations(log)
    passed_through = [line for line in invocations if line.startswith("project show")]
    refused = [line for line in invocations if line.startswith(f"{STUBBED} project show")]
    assert len(passed_through) == LAUNCH_PLAN_READS, (
        "the launch's own plan read has to go through for there to be a run at all; "
        f"the injector saw {invocations}"
    )
    assert refused, (
        "the write-back has to have read its destination for its refusal to be the "
        f"thing under test; the injector saw {invocations}"
    )
    assert not [line for line in invocations if "project copy" in line], (
        "a refused destination read must end the projection before anything is "
        f"written, and this run reached a copy: {invocations}"
    )

    assert record.read_bytes() == before, (
        "the refused projection rewrote the record anyway; a destination read it "
        "cannot trust has to leave the store exactly as it found it"
    )


def test_adopted_archive_binary_and_authoring_ignore_are_in_force() -> None:
    """The host binary reports the pin and the authoring directory cannot be added."""
    version = subprocess.run(
        [str(Path.home() / ".local/bin/onetaskgraph"), "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip() == f"onetaskgraph {ADOPTED}"
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".plans/projects/example.md"],
        cwd=REPO_ROOT,
        check=False,
    )
    assert ignored.returncode == 0
