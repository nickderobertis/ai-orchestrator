"""Drive this checkout's committed plan-store configuration through the real CLI."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from fake_backend import PROMPT_LOG_ENV
from test_orchestrate_launch_e2e import _environment as _launch_environment

from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT

ADOPTED = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()


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
            "name": "launch",
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
            "authoring:launch",
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
