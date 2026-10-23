"""A real copy-plan's routed home remains the spike brief's recorded board home."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import onetaskgraph_user_layer
import plan_fixture_source
import plan_root_variable
import pytest
from github_board import _serving_board
from linear_board import linear_board
from project_fixtures import FIXTURE_UNIT, budgeted, helper, no_budgets, reviewed
from scratch_identity import seeded

from orchestrator import plan_store, run_reading, spike_brief, spike_brief_flow
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT


@pytest.mark.parametrize("destination", ["sandbox-linear", "another-board"])
def test_copy_plan_and_receipt_follow_the_resolved_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    repository = "github.com/synthetic-private/widget"
    identity = seeded(tmp_path / "identity", origin=repository)
    for key, value in {**identity.environment, "ONEVCS_HOME": str(identity.home)}.items():
        monkeypatch.setenv(key, value)
    layer = onetaskgraph_user_layer.example()
    linear = layer["sources"][onetaskgraph_user_layer.ORGANIZATION_PLANS]
    linear["visibility"] = "private"
    layer["sources"] = {
        "plans": {
            "routes": [{"repositories": ["github.com/synthetic-private/*"], "to": destination}]
        },
        destination: linear,
        "authoring": {"visibility": "private"},
    }
    monkeypatch.setenv(
        "XDG_CONFIG_HOME", str(onetaskgraph_user_layer.installed(tmp_path / "config", layer))
    )
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    monkeypatch.setenv(plan_root_variable.name(), str(authoring))
    plan = {
        "schema_version": 3,
        "name": "preview",
        "goal": {"text": "Preview the private interface measurements"},
        "tasks": [
            {
                "id": "measure",
                "kind": "human",
                "repo": repository,
                "task": "Measure the interface.",
                "unit": FIXTURE_UNIT,
            }
        ],
    }
    with _serving_board() as remote, linear_board() as board:
        for key, value in {**remote, **board.environment(destination)}.items():
            monkeypatch.setenv(key, value)
        write_plan_project(authoring, plan, native_id="preview")
        budgeted("authoring", "preview", no_budgets([repository]))
        reviewed("authoring:preview")
        client = plan_store.client()
        plan_store.sdk(client.project_create("authoring", "preview-spikes", "Measurements"))
        plan_store.sdk(
            client.task_create(
                "authoring",
                "preview-spikes",
                "Measure the interface",
                metadata=['onepipeline.id="measure"'],
                body="Measure request behavior.",
            )
        )
        runs = tmp_path / "runs"
        monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(runs))
        monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", "sandbox-manager")
        monkeypatch.setenv("ONEPIPELINE_LAUNCHER", "codex")
        # llmlint: ignore[e2e_not_mocked] Substitutes only the allowed paid model provider.
        monkeypatch.setenv("ONEAGENTGRAPH_ONEHARNESS_BIN", str(helper("fake_backend.py")))
        monkeypatch.setenv("REAL_ONEHARNESS_BIN", str(REPO_ROOT / ".venv/bin/oneharness"))
        commands = tmp_path / "commands.json"
        commands.write_text(
            json.dumps(
                {
                    "Render exactly one spike-brief document": [
                        [
                            str(REPO_ROOT / ".venv/bin/python"),
                            str(Path(__file__).with_name("routed_writer.py")),
                            str(tmp_path),
                        ]
                    ]
                }
            )
        )
        monkeypatch.setenv("FAKE_BACKEND_RUN_ON_MARKER", str(commands))
        monkeypatch.setenv(plan_fixture_source.ROOT_VARIABLE, str(tmp_path / "fixtures"))
        path = runs / "preview" / spike_brief.RECEIPT
        try:
            for run in ("preview", "preview-spikes"):
                write_plan_project(
                    plan_fixture_source.root(),
                    {
                        "schema_version": 3,
                        "name": run,
                        "goal": {"text": "Wait for the preview"},
                        "tasks": [{"id": "hold", "kind": "human", "task": "Read the preview."}],
                    },
                    native_id=run,
                )
                started = subprocess.run(
                    [
                        str(REPO_ROOT / ".venv/bin/onepipeline"),
                        "start",
                        f"{plan_fixture_source.SOURCE}:{run}",
                        "--require-rendered",
                        "false",
                        "--dag-graph",
                        "off",
                        "--detach",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                assert started.returncode == 0, started.stderr
            # Launch and success hook are the public flow: its own ready entry point
            # runs just copy-plan and records the returned home, without a seeded receipt.
            launched = subprocess.run(
                [
                    str(REPO_ROOT / ".venv/bin/python"),
                    "-m",
                    "orchestrator.spike_brief_flow",
                    "launch",
                    "authoring:preview",
                    "preview",
                    "preview-spikes",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
            assert "spike brief advisory:" not in launched.stderr, launched.stderr
            deadline = time.monotonic() + 120
            while True:
                status = subprocess.run(
                    [
                        str(REPO_ROOT / ".venv/bin/onepipeline"),
                        "status",
                        "preview-spike-brief",
                        "--json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                if spike_brief.terminal_reading(
                    status.stdout, run_reading.RunId("preview-spike-brief")
                ):
                    break
                assert time.monotonic() < deadline, status.stdout
                time.sleep(0.1)
            # The host's credential allow-list omits arbitrary user-layer names.
            # Exercise the real recovery users have: resume with the source's credential.
            while True:
                resumed = subprocess.run(
                    [
                        str(REPO_ROOT / ".venv/bin/python"),
                        "-m",
                        "orchestrator.spike_brief_flow",
                        "resume",
                        "preview",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=True,
                )
                if "delivery is already running" not in resumed.stderr:
                    break
                assert time.monotonic() < deadline, resumed.stderr
                time.sleep(0.1)
            assert "spike brief advisory:" not in resumed.stderr, resumed.stderr
            receipt = spike_brief.saved(path)
            assert receipt["brief"] is not None
            source = plan_store.QualifiedProjectId("authoring:preview")
            named = receipt["board_project"]
            assert named == destination + ":" + board.projects()[0]["id"]
            assert spike_brief_flow.board_project(source, named).qualified_id == named
            assert receipt["brief"]["url"] == board.projects()[0]["url"]
            recopied = subprocess.run(
                ["just", "copy-plan", str(source)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
            assert spike_brief_flow.copied_project(recopied.stdout, source) == named
        finally:
            for run in ("preview", "preview-spikes", "preview-spike-brief"):
                if not (runs / run / "launch.json").exists():
                    continue
                stopped = subprocess.run(
                    [
                        str(REPO_ROOT / ".venv/bin/onepipeline"),
                        "stop",
                        run,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                assert stopped.returncode == 0, stopped.stderr
        links = plan_store.project_record(source)["metadata"][plan_store.COPIES_KEY]
        assert links[destination] == named
        assert board.issues()[0]["project"]["id"] == board.projects()[0]["id"]
        for refused in ("authoring:foreign", "unrouted:foreign"):
            with pytest.raises(ValueError, match="board_project"):
                spike_brief.validate({**receipt, "board_project": refused})
        assert (
            spike_brief.validate({**receipt, "board_project": "plans:legacy"})["schema_version"]
            == 1
        )
        # Read a malformed consumer input without changing the live flow's receipt.
        malformed = tmp_path / "consumer-inputs" / "preview" / spike_brief.RECEIPT
        malformed.parent.mkdir(parents=True)
        malformed.write_text(json.dumps({**receipt, "board_project": destination + ":wrong"}))
        with pytest.raises(ValueError, match="board_project disagrees"):
            spike_brief.read(malformed)
        assert spike_brief.saved(path) == receipt
