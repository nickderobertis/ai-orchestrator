"""The installed CLI writes and reads the loopback Linear service."""

from __future__ import annotations

import ast
import base64
import copy
import inspect
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

import linear_board as wire
import onetaskgraph_user_layer
import plan_root_variable
import pytest
from linear_board import LINEAR_REWRITES, Rewrite, linear_board, stored_form
from published_tools import ONETASKGRAPH_BIN

from orchestrator import plan_store
from orchestrator.root import REPO_ROOT


def test_each_documented_rewrite() -> None:
    nodes = ast.parse(inspect.getsource(wire)).body
    declaration = next(
        i
        for i, node in enumerate(nodes)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "LINEAR_REWRITES"
    )
    documentation = ast.literal_eval(nodes[declaration + 1].value)
    documented = re.findall(r'^[\w-]+: (".*") -> (".*")$', documentation, re.M)
    assert len(documented) == len(LINEAR_REWRITES) + 1
    for written, stored in documented:
        assert stored_form(json.loads(written), LINEAR_REWRITES) == json.loads(stored)
    for rule in LINEAR_REWRITES:
        assert stored_form(rule.written, LINEAR_REWRITES) == rule.stored, rule.name
        assert stored_form(rule.stored, LINEAR_REWRITES) == rule.stored, rule.name
        assert stored_form(f"`{rule.written}`", LINEAR_REWRITES) == f"`{rule.written}`"
        fenced = f"```md\n{rule.written}\n```"
        assert stored_form(fenced, LINEAR_REWRITES) == fenced
    protected = "```md\n- [x] ~ _y_ example.com\n```\n`[x] example.com`"
    assert stored_form(protected, LINEAR_REWRITES) == protected
    inline = (
        "`code`- inline\n[link](https://example.com)- inline\n`code`--> inline\ntext | --- | prose"
    )
    assert stored_form(inline, LINEAR_REWRITES) == inline


def test_caller_rewrite_is_stored_and_read_by_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer = onetaskgraph_user_layer.example()
    source = "sandbox-linear"
    layer["sources"] = {source: layer["sources"][onetaskgraph_user_layer.ORGANIZATION_PLANS]}
    monkeypatch.setenv(
        "XDG_CONFIG_HOME", str(onetaskgraph_user_layer.installed(tmp_path / "config", layer))
    )
    body = tmp_path / "body.md"
    body.write_text("- one\n\nunmodeled-token")
    extra = Rewrite("caller-token", lambda text: text.replace("unmodeled-token", "caller-stored"))
    with linear_board(rewrites=(*LINEAR_REWRITES, extra)) as board:
        for key, value in board.environment(source).items():
            monkeypatch.setenv(key, value)

        def cli(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(ONETASKGRAPH_BIN), "--json", *args],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
                timeout=60,
            )

        created = cli(
            "project",
            "create",
            source,
            "--id",
            "caller",
            "--title",
            "Caller rewrite",
            "--body-file",
            str(body),
        )
        # From onetaskgraph 0.12.4 the store records what Linear answers it stored rather
        # than refusing a write Linear rewrote, so the create succeeds on the stored form.
        assert created.returncode == 0, created.stdout + created.stderr
        project = board.projects()[0]
        expected = "* one\n\ncaller-stored"
        assert project["content"] == expected
        returned = cli("project", "show", source + ":" + project["id"])
        assert returned.returncode == 0, returned.stderr
        assert json.loads(returned.stdout)["items"][0]["item"]["content"] == expected
        assert "unmodeled-token" not in returned.stdout


def test_real_store_copy_and_person_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layer = onetaskgraph_user_layer.example()
    source = "sandbox-linear"
    layer["sources"] = {
        source: layer["sources"][onetaskgraph_user_layer.ORGANIZATION_PLANS],
    }
    secondary = copy.deepcopy(layer["sources"][source])
    secondary["config"]["status_mapping"]["todo"] = {
        "task": "Another Todo",
        "project": "Another Planned",
    }
    layer["sources"]["sandbox-secondary"] = secondary
    home = onetaskgraph_user_layer.installed(tmp_path / "config", layer)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.setenv(plan_root_variable.name(), str(tmp_path / "authoring"))
    (tmp_path / "authoring").mkdir()
    with linear_board() as board:
        for key, value in board.environment(source).items():
            monkeypatch.setenv(key, value)
        for key, value in board.environment("sandbox-secondary").items():
            monkeypatch.setenv(key, value)
        environment = os.environ.copy()
        assert environment["HIDDENCO_LINEAR_API_KEY"] == "loopback-not-a-production-credential"

        def cli(*args: str, input: str | None = None) -> str:
            result = subprocess.run(
                [str(ONETASKGRAPH_BIN), "--json", *args],
                cwd=REPO_ROOT,
                env=environment,
                input=input,
                text=True,
                capture_output=True,
                timeout=60,
            )
            assert result.returncode == 0, result.stderr + result.stdout
            return result.stdout

        body = tmp_path / "body.md"
        content = "\n\n".join(rule.written for rule in LINEAR_REWRITES)
        body.write_text(content)
        cli(
            "project",
            "create",
            "authoring",
            "--id",
            "wire",
            "--title",
            "Wire project",
            "--body-file",
            str(body),
        )
        cli(
            "task",
            "create",
            "authoring",
            "--project",
            "wire",
            "--title",
            "Wire task",
            "--body-file",
            str(body),
        )
        cli(
            "document",
            "create",
            "authoring",
            "--project",
            "wire",
            "--id",
            "doc",
            "--title",
            "Wire document",
            "--body-file",
            str(body),
        )
        cli("project", "copy", "authoring:wire", "--to", source)
        cli("document", "copy", "authoring:doc", "--to", source)
        assert len(board.issues()) == len(board.projects()) == len(board.documents()) == 1
        task_origin = json.loads(cli("task", "list", "--source", "authoring", "--project", "wire"))[
            "items"
        ][0]["id"]
        for item, field, title, origin in [
            (board.issues()[0], "description", "Wire task", task_origin),
            (board.projects()[0], "content", "Wire project", "authoring:wire"),
            (board.documents()[0], "content", "Wire document", "authoring:doc"),
        ]:
            assert item["title"] == title
            assert item["metadata"]["onetaskgraph.origin"] == origin
            metadata = json.dumps(item["metadata"], sort_keys=True, separators=(",", ":"))
            assert item[field] == (
                stored_form(content, LINEAR_REWRITES)
                + "\n\n<!-- onetaskgraph.metadata `"
                + metadata
                + "` -->"
            )
        task = source + ":" + board.issues()[0]["id"]
        cli("task", "status", "set", task, "done")
        settings = plan_store.configured_settings()
        assert (
            board.issues()[0]["state"]["name"]
            == settings[f"sources.{source}.config.status_mapping.done.task"]
        )
        comment = "<!-- marker example.com [x] -->"
        cli("task", "comment", "add", task, input=comment)
        assert comment in cli("task", "comment", "list", task)
        board.edit(board.issues()[0]["id"], "A person edited this")
        assert "A person edited this" in cli("task", "show", task)
        assert board.requests()[0]["operation"]

        unknown = subprocess.run(
            [
                str(ONETASKGRAPH_BIN),
                "--set",
                f"sources.{source}.config.status_mapping.todo.task=New State",
                "sources",
                "fields",
                source,
                "--apply",
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert unknown.returncode != 0
        assert "unsupported operation workflowStateCreate" in unknown.stderr
        assert "A person edited this" in cli("task", "show", task)
        request = urllib.request.Request(
            board.endpoint,
            data=json.dumps(
                {
                    "query": "query FutureOperation { futureEntity { id } }",
                    "operationName": "FutureOperation",
                    "variables": {},
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.load(response)["errors"] == [
                {"message": "unsupported operation FutureOperation"}
            ]

        comment_id = board.issues()[0]["comments"]["nodes"][0]["id"]
        cli("task", "comment", "edit", task, comment_id, input="A corrected comment")
        assert "A corrected comment" in cli("task", "comment", "list", task)
        cli("task", "comment", "delete", task, comment_id)
        assert not board.issues()[0]["comments"]["nodes"]

        for body_text in ("First ordered comment", "Second ordered comment"):
            cli("task", "comment", "add", task, input=body_text)
        ordered = cli("task", "comment", "list", task)
        assert ordered.index("First ordered comment") < ordered.index("Second ordered comment")
        for held in board.issues()[0]["comments"]["nodes"]:
            cli("task", "comment", "delete", task, held["id"])

        cli(
            "task",
            "create",
            source,
            "--project",
            board.projects()[0]["id"],
            "--title",
            "Dependent task",
            "--body-file",
            str(body),
            "--depends-on",
            task,
        )
        dependent = (
            source
            + ":"
            + next(item["id"] for item in board.issues() if item["title"] == "Dependent task")
        )
        assert board.issues()[0]["id"] in cli("task", "deps", dependent)
        cli("task", "update", dependent, "--no-depends-on")
        assert json.loads(cli("task", "deps", dependent))["items"] == []

        cli("project", "create", "authoring", "--id", "dependent", "--title", "Dependent project")
        project_file = tmp_path / "authoring/projects/dependent.md"
        authored = project_file.read_text()
        project_file.write_text(authored.replace("---\n", "---\ndepends_on: [wire]\n", 1))
        cli("project", "copy", "authoring:dependent", "--to", source)
        project = (
            source
            + ":"
            + next(item["id"] for item in board.projects() if item["title"] == "Dependent project")
        )
        assert board.projects()[0]["id"] in cli("project", "deps", project)
        project_file.write_text(authored)
        cli("project", "copy", "authoring:dependent", "--to", source)
        assert json.loads(cli("project", "deps", project))["items"] == []

        pixel = tmp_path / "pixel.png"
        pixel.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
            )
        )
        asset_body = tmp_path / "asset.md"
        asset_body.write_text("![pixel](./pixel.png)")
        cli(
            "task",
            "create",
            "authoring",
            "--project",
            "wire",
            "--title",
            "Asset task",
            "--body-file",
            str(asset_body),
            "--asset",
            str(pixel),
        )
        cli("project", "copy", "authoring:wire", "--to", source)
        asset_issue = next(item for item in board.issues() if item["title"] == "Asset task")
        url = re.search(r"http://127\.0\.0\.1:[0-9]+/assets/[a-z0-9-]+", asset_issue["description"])
        assert url is not None
        with urllib.request.urlopen(url[0], timeout=5) as uploaded:
            assert uploaded.read() == pixel.read_bytes()
        assert url[0].startswith(board.endpoint.removesuffix("/graphql") + "/assets/")
        asset_path = url[0].removeprefix(board.endpoint.removesuffix("/graphql"))
        requests = board.requests()
        upload = next(
            i for i, request in enumerate(requests) if request["operation"] == "AssetUpload"
        )
        assert requests[upload + 1] == {"operation": "PUT", "variables": {"path": asset_path}}
        assert requests[-1] == {"operation": "GET", "variables": {"path": asset_path}}


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses the filesystem write refusal")
@pytest.mark.parametrize("kind", ["project", "task", "document"])
def test_a_source_link_failure_rolls_back_the_created_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    layer = onetaskgraph_user_layer.example()
    source = "sandbox-linear"
    layer["sources"] = {source: layer["sources"][onetaskgraph_user_layer.ORGANIZATION_PLANS]}
    monkeypatch.setenv(
        "XDG_CONFIG_HOME", str(onetaskgraph_user_layer.installed(tmp_path / "config", layer))
    )
    root = tmp_path / "authoring"
    root.mkdir()
    monkeypatch.setenv(plan_root_variable.name(), str(root))
    body = tmp_path / "body.md"
    body.write_text("unmodeled-token")

    def command(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ONETASKGRAPH_BIN), "--json", *args],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=60,
        )

    created = command(
        "project",
        "create",
        "authoring",
        "--id",
        "rollback",
        "--title",
        "Rollback",
        *(["--body-file", str(body)] if kind == "project" else []),
    )
    assert created.returncode == 0, created.stderr
    if kind in ("task", "document"):
        created = command(
            kind,
            "create",
            "authoring",
            "--project",
            "rollback",
            "--title",
            "Rollback item",
            "--body-file",
            str(body),
            *(["--id", "rollback-doc"] if kind == "document" else []),
        )
        assert created.returncode == 0, created.stderr
    with linear_board() as board:
        for key, value in board.environment(source).items():
            monkeypatch.setenv(key, value)
        if kind == "document":
            copied = command("project", "copy", "authoring:rollback", "--to", source)
            assert copied.returncode == 0, copied.stderr
        protected = root / {"task": "tasks", "project": "projects", "document": "documents"}[kind]
        mode = protected.stat().st_mode & 0o777
        try:
            protected.chmod(0o555)
            refused = (
                command("document", "copy", "authoring:rollback-doc", "--to", source)
                if kind == "document"
                else command("project", "copy", "authoring:rollback", "--to", source)
            )
        finally:
            protected.chmod(mode)
        assert refused.returncode != 0
        assert str(protected) in refused.stderr + refused.stdout
        operation = {
            "task": "issueDelete",
            "project": "projectDelete",
            "document": "documentDelete",
        }[kind]
        assert operation in [request["operation"] for request in board.requests()]
        assert not {"task": board.issues, "project": board.projects, "document": board.documents}[
            kind
        ]()
