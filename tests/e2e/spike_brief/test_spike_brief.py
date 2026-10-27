"""The pinned template/store, the receipt's public validation boundary, and the flow's
entry points run in process.

The commands — `just plan`, which launches and resumes the brief through
`python -m orchestrator.spike_brief_flow`, `just approve-spikes` and `just unfinished` — are
driven as commands end to end in `tests/e2e/spike_brief/test_plan_journey.py`, inside a copy
of this checkout. This module is the in-process half: the coverage floor over
`orchestrator/` is measured in process and a subprocess proves nothing to it, so here the
same entry points are called in this process, over the same real engine, store and channel,
with only the paid provider and GitHub's API stood in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from orchestrator import npm_installs, run_reading, spike_brief
from orchestrator.root import REPO_ROOT


def flow(capsys: pytest.CaptureFixture[str], *argv: str) -> subprocess.CompletedProcess[str]:
    """`python -m orchestrator.spike_brief_flow <argv>`, its entry point run in this process.

    The command line, environment, engine and store are the real ones the flow's callers
    use; running the entry point here rather than in a child is what lets the measuring
    tier see the lines it runs.
    """
    from orchestrator import spike_brief_flow

    capsys.readouterr()
    # llmlint: ignore-block[e2e_not_mocked, tests_mirror_real_usage] The in-process half the
    # module docstring names: the command line is the real one, run in this process so the
    # coverage floor measures it; test_plan_journey.py runs the same command as a command.
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(sys, "argv", ["spike_brief_flow", *argv])
        status = spike_brief_flow.main()
    # llmlint: ignore-end[e2e_not_mocked, tests_mirror_real_usage]
    captured = capsys.readouterr()
    return subprocess.CompletedProcess(list(argv), status, captured.out, captured.err)


def script_paid_words(monkeypatch: pytest.MonkeyPatch) -> None:
    from project_fixtures import helper

    # llmlint: ignore[e2e_not_mocked] Only the paid model's words are scripted.
    monkeypatch.setenv("ONEAGENTGRAPH_ONEHARNESS_BIN", str(helper("fake_backend.py")))
    monkeypatch.setenv("REAL_ONEHARNESS_BIN", str(REPO_ROOT / ".venv/bin/oneharness"))


def _render(tmp_path: Path, overview: str, rows: list[dict[str, str]]) -> str:
    loader = subprocess.run(
        [
            str(npm_installs.ENGINE),
            "template",
            "resolve",
            "spike-brief",
            "--template-root",
            str(REPO_ROOT / "templates"),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    loaded = tmp_path / "loader.json"
    loaded.write_text(loader.stdout)
    supplied = tmp_path / "answers.json"
    supplied.write_text(json.dumps({"overview": overview, "spikes": rows}))
    result = subprocess.run(
        [
            str(REPO_ROOT / ".venv/bin/onetaskgraph"),
            "template",
            "render",
            "--template-loader",
            str(loaded),
            "--answers",
            str(supplied),
            "--no-interactive",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout


def _rows() -> list[dict[str, str]]:
    return [
        {
            "spike": f"spike-agent-{index}",
            "question": "Which approach preserves the required interface? " * 8,
            "use": "Choose the implementation from measured behavior.",
        }
        for index in range(6)
    ]


def test_independent_caps_allow_a_long_total(tmp_path: Path) -> None:
    rows = _rows()
    overview = (
        "During planning, measure the candidate interfaces to choose feasible implementations."
    )
    rendered = _render(tmp_path, overview, rows)
    assert len(rendered.split()) > 250
    spike_brief.answers(
        {"overview": overview, "spikes": rows}, [row["spike"] for row in rows], rendered
    )
    assert rendered.strip().startswith(overview)
    assert [line for line in rendered.splitlines() if line.startswith("## ")] == [
        "## " + row["spike"] for row in rows
    ]


@pytest.mark.parametrize(
    "fault", ["overview", "section", "placeholder", "duplicate", "missing", "grouped", "empty"]
)
def test_rendered_bad_answers_are_refused(tmp_path: Path, fault: str) -> None:
    rows = _rows()
    agents = [row["spike"] for row in rows]
    overview = "Measure planning alternatives so implementation can use the supported interface."
    match fault:
        case "overview":
            overview = "measurement " * 251
        case "section":
            rows[0]["question"] = "measurement " * 251
        case "placeholder":
            rows[0]["use"] = "TBD"
        case "duplicate":
            rows[1]["spike"] = rows[0]["spike"]
        case "missing":
            rows.pop()
        case "grouped":
            rows[0]["spike"] += ", " + rows.pop()["spike"]
        case "empty":
            rows[0]["question"] = ""
    rendered = _render(tmp_path, overview, rows)
    with pytest.raises(ValueError):
        spike_brief.answers({"overview": overview, "spikes": rows}, agents, rendered)


@pytest.mark.parametrize("fault", ["heading", "overview"])
def test_answer_boundary_refuses_a_rendering_that_disagrees_with_answers(
    tmp_path: Path, fault: str
) -> None:
    rows = _rows()
    overview = "Measure the planning interfaces before choosing implementation."
    rendering = _render(tmp_path, overview, rows)
    supplied: dict[str, object] = {"overview": overview, "spikes": rows}
    match fault:
        case "heading":
            supplied["spikes"] = list(reversed(rows))
        case "overview":
            supplied["overview"] = overview + " Compare the rejected request."
    expected = "stored headings" if fault == "heading" else "start with the overview"
    with pytest.raises(ValueError, match=expected):
        spike_brief.answers(supplied, [row["spike"] for row in rows], rendering)


def test_receipt_round_trip_and_advisory_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = json.loads((Path(__file__).parent / "receipt-v1.json").read_text())
    path = tmp_path / fixture["parent_run"] / spike_brief.RECEIPT
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path))
    assert spike_brief.read(path) is None
    with spike_brief.locked(path):
        spike_brief.write(path, spike_brief.validate(fixture))
    assert spike_brief.read(path) == fixture
    assert spike_brief.items("unrelated-session") == ([], [])
    fixture["schema_version"] = True
    path.write_text(json.dumps(fixture))
    rows, diagnoses = spike_brief.items("manager-session")
    assert rows == []
    assert "integer 1" in diagnoses[0]


@pytest.mark.parametrize(
    "refuse_ack",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.geteuid() == 0, reason="root bypasses the real filesystem permission refusal"
            ),
        ),
    ],
)
def test_engine_launches_one_detached_codex_writer_for_six_actual_agents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    refuse_ack: bool,
) -> None:
    import os
    import time

    from orchestrator import plan_store

    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", "spike-brief-probe")
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER", "codex")
    (tmp_path / "authoring").mkdir()
    # Imported here: the backend runs this module as a script, without `tests/`.
    import plan_root_variable

    monkeypatch.setenv(plan_root_variable.name(), str(tmp_path / "authoring"))
    script_paid_words(monkeypatch)
    gate = tmp_path / "gate"
    gate.mkdir()
    monkeypatch.setenv("FAKE_BACKEND_TURN_GATE", str(gate))
    monkeypatch.setenv("FAKE_BACKEND_PROMPT_LOG", str(tmp_path / "prompts"))
    client = plan_store.client()
    plan_store.sdk(client.project_create("authoring", "probe-spikes", "Six actual spike agents"))
    for index in range(6):
        plan_store.sdk(
            client.task_create(
                "authoring",
                "probe-spikes",
                f"spike-agent-{index}",
                metadata=[f'onepipeline.id="spike-agent-{index}"'],
                body="Measure the supported interface and report its behavior.",
            )
        )
    writer = "probe-spike-brief"
    try:
        launched = flow(capsys, "launch", "authoring:probe", "probe", "probe-spikes")
        assert launched.returncode == 0 and "advisory:" not in launched.stderr, launched.stderr
        deadline = time.monotonic() + 30
        while not (gate / "reached").exists():
            assert time.monotonic() < deadline, "paid-provider rendezvous never arrived"
            time.sleep(0.05)
        receipt = spike_brief.read(tmp_path / "runs/probe" / spike_brief.RECEIPT)
        assert receipt is not None and receipt["writer_run"] == writer
        # The writer's run is a `spike-brief` run grouped under the plan it briefs, which
        # its own writer project does not name.
        record = json.loads((tmp_path / "runs" / writer / "launch.json").read_text("utf-8"))
        assert record.get("labels") == {"kind": "spike-brief"}, record
        assert record.get("group") == "authoring:probe", record
        assert record["project"] == f"authoring:{writer}", record
        # The parent's receipt names its spikes run; a launch naming another is refused.
        foreign = flow(capsys, "launch", "authoring:probe", "probe", "probe-other-spikes")
        assert "existing receipt belongs to another flow" in foreign.stderr, foreign.stderr
        assert spike_brief.read(tmp_path / "runs/probe" / spike_brief.RECEIPT) == receipt
        amended = subprocess.run(
            [str(npm_installs.ENGINE), "reply", writer],
            input=json.dumps(
                {
                    "version": 3,
                    "commands": [
                        {"op": "amend", "id": "spike-brief", "text": "Keep the local brief goal."}
                    ],
                }
            ),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert "applied" in amended.stdout
        owed = subprocess.run(
            [
                str(npm_installs.ENGINE),
                "unwatched",
                "--session",
                "spike-brief-probe",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert owed.returncode == 6 and writer in owed.stdout
        if refuse_ack:
            acknowledgements = tmp_path / "runs" / writer / "acknowledgements"
            modes = {
                held: held.stat().st_mode & 0o777
                for held in [*acknowledgements.rglob("*"), acknowledgements]
            }
            before = (tmp_path / "runs/probe" / spike_brief.RECEIPT).read_bytes()
            try:
                for held, mode in modes.items():
                    held.chmod(mode & ~0o222)
                refused = flow(capsys, "resume", "probe")
                assert refused.returncode == 0
                assert refused.stderr.count("acknowledgement failed") == 2, refused.stderr
                assert "Permission denied" in refused.stderr
                assert (tmp_path / "runs/probe" / spike_brief.RECEIPT).read_bytes() == before
            finally:
                for held, mode in modes.items():
                    held.chmod(mode)
        resumed = flow(capsys, "resume", "probe")
        assert resumed.returncode == 0 and "acknowledgement failed" not in resumed.stderr
        (gate / "released").touch()
        while not (tmp_path / "prompts").exists():
            assert time.monotonic() < deadline, "provider did not record its request"
            time.sleep(0.05)
        prompts = [json.loads(line) for line in (tmp_path / "prompts").read_text().splitlines()]
        effective = subprocess.run(
            [str(REPO_ROOT / ".venv/bin/oneharness"), "config", "--config", prompts[0]["config"]],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert "gpt-6.1-sol" in effective.stdout
        assert "local authoring project authoring:probe" in str(prompts)
        assert all(f"spike-agent-{index}" in str(prompts) for index in range(6))
        launched = flow(capsys, "launch", "authoring:probe", "probe", "probe-spikes")
        assert launched.returncode == 0 and "advisory:" not in launched.stderr, launched.stderr
        assert len(list((tmp_path / "runs").glob("*/launch.json"))) == 1
        answer = subprocess.run(
            [
                str(npm_installs.ENGINE),
                "unwatched",
                "--session",
                "spike-brief-probe",
            ],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert answer.returncode == 0, answer.stdout + answer.stderr
    finally:
        (gate / "released").touch()
        if (tmp_path / "runs" / writer / "launch.json").exists():
            stopped = subprocess.run(
                [str(npm_installs.ENGINE), "stop", writer],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            assert stopped.returncode == 0, stopped.stderr


def test_a_refused_writer_launch_resumes_onto_what_the_first_attempt_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """History that cannot start writes no receipt; a refused start keeps its writer project.

    The first launch finds its delivery history's path taken and stops before any receipt;
    the second finds a writer project an interrupted preparation left with a task the
    engine will not start, and is refused; once that task is regenerated, the third starts
    the writer on the project and task already there, keeping the eligibility it recorded.
    """
    import plan_root_variable

    from orchestrator import design_approval, plan_store
    from orchestrator.project_store import write_plan_project

    runs = tmp_path / "runs"
    (tmp_path / "authoring").mkdir()
    monkeypatch.setenv(plan_root_variable.name(), str(tmp_path / "authoring"))
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(runs))
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", "spike-brief-retry")
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER", "codex")
    script_paid_words(monkeypatch)
    gate = tmp_path / "gate"
    gate.mkdir()
    monkeypatch.setenv("FAKE_BACKEND_TURN_GATE", str(gate))
    client = plan_store.client()
    plan_store.sdk(client.project_create("authoring", "retry-spikes", "Six actual spike agents"))
    for row in _rows():
        plan_store.sdk(
            client.task_create(
                "authoring",
                "retry-spikes",
                row["spike"],
                metadata=["onepipeline.id=" + json.dumps(row["spike"])],
                body="Measure the supported interface and report its behavior.",
            )
        )
    path = runs / "retry" / spike_brief.RECEIPT
    history = path.parent / spike_brief.DELIVERY_HISTORY
    writer = "retry-spike-brief"
    try:
        history.mkdir(parents=True)
        unstarted = flow(capsys, "launch", "authoring:retry", "retry", "retry-spikes")
        assert "spike brief advisory:" in unstarted.stderr, unstarted.stderr
        assert not path.exists()
        history.rmdir()
        write_plan_project(
            plan_store.source_root("authoring"),
            {
                "schema_version": 3,
                "name": writer,
                "goal": {"text": "Write the local spike brief"},
                "tasks": [
                    {
                        "id": "spike-brief",
                        "persona": "../personas/spike-brief.yaml",
                        "agent_graph": "graphs/spike-brief.yaml",
                        "max_turns": 3,
                        "task": "Interrupted preparation omitted the required criteria.",
                    }
                ],
            },
            native_id=writer,
            project_metadata={
                design_approval.PLAN_KIND: {"kind": "planning", "nodes": ["spike-brief"]}
            },
        )
        refused = flow(capsys, "launch", "authoring:retry", "retry", "retry-spikes")
        assert "brief writer launch refused" in refused.stderr, refused.stderr
        eligible = json.loads(history.read_text())["eligible"]
        loader = tmp_path / "plan-task-loader.json"
        loader.write_text(
            _engine(
                "template",
                "resolve",
                "plan-task",
                "--template-root",
                str(REPO_ROOT / "templates"),
                "--json",
            ).stdout
        )
        held = plan_store.read_tasks("authoring:" + writer)
        plan_store.sdk(
            client.task_render(
                str(held[0].qualified_id),
                template_loader=str(loader),
                answers={
                    "what": "Read local authoring project authoring:retry",
                    "why": "Make the measurements visible.",
                    "acceptance_criteria": ["Write the local brief with six agent sections."],
                    "additional_info": "Write only local authoring records outside tracked files.",
                    "budgets": [],
                },
            )
        )
        started = flow(capsys, "launch", "authoring:retry", "retry", "retry-spikes")
        assert "advisory:" not in started.stderr, started.stderr
        assert (runs / writer / "launch.json").exists()
        assert [
            str(task.qualified_id) for task in plan_store.read_tasks("authoring:" + writer)
        ] == [str(held[0].qualified_id)]
        assert json.loads(history.read_text())["eligible"] == eligible
    finally:
        (gate / "released").touch()
        if (runs / writer / "launch.json").exists():
            assert _engine("stop", writer).returncode == 0


@pytest.mark.parametrize(
    "verb,arguments,field",
    [
        ("launch", ["../unsafe", "safe", "safe-spikes"], "plan_project"),
        ("launch", ["plans:safe", "safe", "safe-spikes"], "plan_project must belong to authoring"),
        ("resume", ["../unsafe"], "parent_run"),
        ("hook", [], "writer_run"),
    ],
)
def test_bad_flow_boundary_input_is_advisory_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    verb: str,
    arguments: list[str],
    field: str,
) -> None:
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path))
    monkeypatch.delenv("ONEPIPELINE_RUN_ID", raising=False)
    result = flow(capsys, verb, *arguments)
    assert result.returncode == 0
    assert "spike brief advisory:" in result.stderr and field in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_zero_spike_draft_launches_no_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path))
    result = flow(capsys, "launch", "authoring:absent-draft", "absent-draft", "absent-draft-spikes")
    assert result.returncode == 0 and result.stdout == "" and result.stderr == ""
    # With no receipt, neither a resume nor a writer's hook has a brief to deliver.
    assert flow(capsys, "resume", "absent-draft").stderr == ""
    monkeypatch.setenv("ONEPIPELINE_RUN_ID", "absent-draft-spike-brief")
    assert flow(capsys, "hook").stderr == ""
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("fault", ["none", "not-a-document", "no-digest", "another-template"])
def test_the_resolved_template_digest_is_held_to_the_spike_brief_template(
    tmp_path: Path, fault: str
) -> None:
    from orchestrator import spike_brief_flow

    resolved = subprocess.run(
        [
            str(npm_installs.ENGINE),
            "template",
            "resolve",
            "spike-brief",
            "--template-root",
            str(REPO_ROOT / "templates"),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    loader: object = json.loads(resolved.stdout)
    assert isinstance(loader, dict)
    match fault:
        case "not-a-document":
            loader = [loader]
        case "no-digest":
            loader.pop("digest")
        case "another-template":
            loader["reference"] = "onepipeline:spike-report"
    if fault == "none":
        assert spike_brief_flow.template_digest(loader) == loader["digest"]
        return
    expected = {
        "not-a-document": "returned no digest",
        "no-digest": "returned no digest",
        "another-template": "returned another template",
    }
    with pytest.raises(ValueError, match=expected[fault]):
        spike_brief_flow.template_digest(loader)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("plan_project", "plans:preview"),
        ("board_project", "authoring:preview"),
        ("parent_run", "../preview"),
        ("writer_run", "bad run"),
        ("spikes_run", ""),
        ("manager_session", "\n"),
        ("brief", {}),
        ("approval", {"digest": "ABC", "spikes_run": "preview-spikes", "approved_at": "today"}),
    ],
)
def test_receipt_reader_refuses_foreign_or_malformed_contract_fields(
    tmp_path: Path, field: str, value: object
) -> None:
    fixture = json.loads((Path(__file__).parent / "receipt-v1.json").read_text())
    fixture[field] = value
    path = tmp_path / "preview" / spike_brief.RECEIPT
    path.parent.mkdir()
    path.write_text(json.dumps(fixture))
    with pytest.raises(ValueError):
        spike_brief.read(path)


def test_section_cap_counts_the_heading_and_labels(tmp_path: Path) -> None:
    rows = _rows()
    overview = "Measure the planning interfaces before choosing implementation."
    rows[0]["question"] = "measurement"
    rendering = _render(tmp_path, overview, rows)
    first = rendering.split("## ")[1].split()
    rows[0]["question"] = " ".join(["measurement"] * (251 - len(first)))
    rendering = _render(tmp_path, overview, rows)
    first = rendering.split("## ")[1].split()
    assert len(first) + 1 == 251  # The split removed the heading's ## word.
    with pytest.raises(ValueError, match="spike-agent-0 exceeds"):
        spike_brief.answers(
            {"overview": overview, "spikes": rows}, [row["spike"] for row in rows], rendering
        )


def test_template_and_validator_have_the_same_independent_product_caps() -> None:
    template = (REPO_ROOT / "templates/spike-brief.md.j2").read_text()
    assert f"at most {spike_brief.WORD_CAP} whitespace-separated words" in template
    assert f"has its own {spike_brief.WORD_CAP}-whitespace-word cap" in template
    assert "there is no total word cap" in template


def test_ready_hook_missing_interpreter_is_advisory(tmp_path: Path) -> None:
    import shutil

    script = tmp_path / "scripts/spike-brief-ready.sh"
    script.parent.mkdir()
    shutil.copy2(REPO_ROOT / "scripts/spike-brief-ready.sh", script)
    result = subprocess.run([str(script)], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0
    assert "spike brief advisory: Python is absent" in result.stderr
    assert "just bootstrap" in result.stderr
    assert not (tmp_path / "runs").exists()


def test_ready_hook_whose_module_cannot_start_is_advisory(tmp_path: Path) -> None:
    """A checkout whose interpreter runs but whose flow module will not load is reported."""
    import shutil

    script = tmp_path / "scripts/spike-brief-ready.sh"
    script.parent.mkdir()
    shutil.copy2(REPO_ROOT / "scripts/spike-brief-ready.sh", script)
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(REPO_ROOT / ".venv/bin/python")
    # The checkout's own package comes first on the module path and lacks the flow module.
    (tmp_path / "orchestrator").mkdir()
    (tmp_path / "orchestrator/__init__.py").write_text("")
    result = subprocess.run([str(script)], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert "No module named orchestrator.spike_brief_flow" in result.stderr
    assert "spike brief advisory: the ready hook exited 1" in result.stderr
    assert "just bootstrap" in result.stderr


@pytest.mark.parametrize(
    "url",
    [
        "https://:443/x",
        "https://host:bad/x",
        "https://@host/x",
        "https://host/a b",
        "https://host:0/x",
    ],
)
def test_public_receipt_validator_refuses_unreadable_https_links(url: str) -> None:
    receipt = json.loads((Path(__file__).parent / "receipt-v1.json").read_text())
    receipt["board_project"] = "plans:board"
    receipt["brief"] = {
        "document": "authoring:preview-brief",
        "url": url,
        "digest": "0" * 64,
        "surfaced_at": "2026-10-13T00:00:00Z",
    }
    with pytest.raises(ValueError):
        spike_brief.validate(receipt)


def test_public_reader_refuses_board_identity_without_ordinary_copy_correspondence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator import plan_store

    authoring = tmp_path / "authoring"
    authoring.mkdir()
    # Imported here: the backend runs this module as a script, without `tests/`.
    import plan_root_variable

    monkeypatch.setenv(plan_root_variable.name(), str(authoring))
    plan_store.sdk(plan_store.client().project_create("authoring", "preview", "Local draft"))
    receipt = json.loads((Path(__file__).parent / "receipt-v1.json").read_text())
    receipt["board_project"] = "plans:unrelated"
    path = tmp_path / "preview" / spike_brief.RECEIPT
    path.parent.mkdir()
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="disagrees with store correspondence"):
        spike_brief.read(path)


def test_design_filter_reference_matches_the_real_brief_rendering_identity() -> None:
    from orchestrator import design_approval

    assert design_approval.SPIKE_BRIEF_REFERENCE == spike_brief.TEMPLATE_REFERENCE


def write_invalid_document(tmp_path: Path, fault: str) -> None:
    """Commands scripted as the paid writer, against the real local document store."""
    import shutil

    from pydantic import JsonValue

    from orchestrator import plan_store

    client = plan_store.client()
    rows = _rows()
    overview = "During planning, measure the supported interfaces before selecting implementation."
    match fault:
        case "placeholder":
            rows[0]["use"] = "TBD"
        case "overview":
            overview = "measurement " * 251
        case "section":
            rows[0]["question"] = "measurement " * 251
    _render(tmp_path, overview, rows)
    loader_file = tmp_path / "loader.json"
    if fault == "obsolete":
        old_root = tmp_path / "old-templates"
        old_root.mkdir()
        for filename in ["templates.yaml", "spike-brief.md.j2"]:
            shutil.copy2(REPO_ROOT / "templates" / filename, old_root / filename)
        old_template = old_root / "spike-brief.md.j2"
        old_template.write_text(old_template.read_text() + "\n{# Previous guidance revision. #}\n")
        resolved = subprocess.run(
            [
                str(npm_installs.ENGINE),
                "template",
                "resolve",
                "spike-brief",
                "--template-root",
                str(old_root),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        loader_file.write_text(resolved.stdout)
    supplied: dict[str, JsonValue] = {
        "overview": overview,
        "spikes": [
            {"spike": row["spike"], "question": row["question"], "use": row["use"]} for row in rows
        ],
    }
    if fault != "missing":
        for native in (
            ["preview-brief", "other-brief"] if fault == "duplicate" else ["preview-brief"]
        ):
            plan_store.sdk(
                client.document_create(
                    "authoring",
                    "preview",
                    "Local brief",
                    id=native,
                    template_loader=str(loader_file),
                    answers=supplied,
                )
            )
    if fault == "edited":
        # llmlint: ignore-block[tests_mirror_real_usage] The refusal under test guards against
        # exactly this: a body changed on disk outside the store's render, which no store verb
        # can produce, so the edit is made where such an edit would be made.
        held = plan_store.read_documents("authoring:preview")[0]
        location = Path(plan_store.located(held.location, str(held.qualified_id)))
        assert location.is_absolute() and location.is_file()
        stored = location.read_text()
        assert stored.count(held.content) == 1
        location.write_text(
            stored.replace(held.content, held.content + "\nUnrendered correction.\n")
        )
        # llmlint: ignore-end[tests_mirror_real_usage]


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "obsolete", "placeholder", "overview", "section", "edited"]
)
def test_real_stored_document_boundary_refuses_invalid_briefs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    from orchestrator import plan_store

    authoring = tmp_path / "authoring"
    authoring.mkdir()
    # Imported here: the backend runs this module as a script, without `tests/`.
    import plan_root_variable

    monkeypatch.setenv(plan_root_variable.name(), str(authoring))
    client = plan_store.client()
    for project in ["preview", "preview-spikes"]:
        plan_store.sdk(client.project_create("authoring", project, project))
    rows = _rows()
    for row in rows:
        plan_store.sdk(
            client.task_create(
                "authoring",
                "preview-spikes",
                row["spike"],
                metadata=["onepipeline.id=" + json.dumps(row["spike"])],
                body="Measure the valid and rejected request behavior.",
            )
        )
    import time

    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", "invalid-document-manager")
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER", "codex")
    script_paid_words(monkeypatch)
    choices = tmp_path / "choices.json"
    choices.write_text(
        json.dumps(
            {
                "Render exactly one spike-brief document": [
                    [
                        str(REPO_ROOT / ".venv/bin/python"),
                        str(Path(__file__).resolve()),
                        str(tmp_path),
                        fault,
                    ]
                ]
            }
        )
    )
    monkeypatch.setenv("FAKE_BACKEND_RUN_ON_MARKER", str(choices))
    launched = flow(capsys, "launch", "authoring:preview", "preview", "preview-spikes")
    assert launched.returncode == 0 and "advisory:" not in launched.stderr, launched.stderr
    path = tmp_path / "runs/preview" / spike_brief.RECEIPT
    receipt = spike_brief.read(path)
    assert receipt is not None
    deadline = time.monotonic() + 60
    while True:
        status = subprocess.run(
            [
                str(npm_installs.ENGINE),
                "status",
                receipt["writer_run"],
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        if spike_brief.terminal_reading(status.stdout, run_reading.RunId(receipt["writer_run"])):
            break
        assert time.monotonic() < deadline, status.stdout
        time.sleep(0.1)
    while True:
        resumed = flow(capsys, "resume", receipt["parent_run"])
        if "delivery is already running" not in resumed.stderr:
            break
        assert time.monotonic() < deadline, resumed.stderr
        time.sleep(0.1)
    assert resumed.returncode == 0 and "spike brief advisory:" in resumed.stderr
    assert "acknowledgement failed" not in resumed.stderr, resumed.stderr
    expected = {
        "missing": "expected exactly one",
        "duplicate": "expected exactly one",
        "obsolete": "obsolete template",
        "placeholder": "non-placeholder",
        "overview": "overview exceeds",
        "section": "spike-agent-0 exceeds",
        "edited": "body was edited after rendering",
    }
    assert expected[fault] in resumed.stderr, resumed.stderr
    assert json.loads(path.read_text())["brief"] is None


def _engine(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(npm_installs.ENGINE), *argv],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _holding_run(run: str) -> None:
    """Start ``run`` as a real run waiting on one human action, so it stays nonterminal.

    A run is named for the project it starts from, and `authoring` already holds the plan
    and spikes projects under these names, so the stand-in is the suite's own
    `test-fixtures` source's project of the same name.
    """
    import plan_fixture_source

    from orchestrator.project_store import write_plan_project

    write_plan_project(
        plan_fixture_source.root(),
        {
            "schema_version": 3,
            "name": run,
            "goal": {"text": "Stand in for a live planning stage"},
            "tasks": [{"id": "hold", "kind": "human", "task": "Hold this stage open."}],
        },
        native_id=run,
    )
    started = _engine(
        "start",
        f"{plan_fixture_source.SOURCE}:{run}",
        "--require-rendered",
        "false",
        "--dag-graph",
        "off",
        "--detach",
    )
    assert started.returncode == 0, started.stderr
    assert (spike_brief.runs_root() / run / "launch.json").exists(), started.stdout


def _wait_until(predicate: Callable[[], bool], what: str) -> None:
    import time

    deadline = time.monotonic() + 120
    while not predicate():
        assert time.monotonic() < deadline, what
        time.sleep(0.1)


def _resumed(capsys: pytest.CaptureFixture[str], parent: str) -> subprocess.CompletedProcess[str]:
    """Resume ``parent``'s delivery once the writer's own hook has let go of the receipt."""
    held: list[subprocess.CompletedProcess[str]] = []

    def answered() -> bool:
        held[:] = [flow(capsys, "resume", parent)]
        return "delivery is already running" not in held[0].stderr

    _wait_until(answered, "the writer's ready hook never released the receipt")
    return held[0]


@pytest.mark.parametrize("ending", ["live", "ended", "late", "unsurfaced"])
def test_delivery_copies_once_surfaces_once_and_retires_its_advisory_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ending: str
) -> None:
    """The writer's hook, then a resume, carry a valid brief through the ordinary copy.

    The board answers only through its GitHub stand-in and the writer's words only through
    the scripted provider; the store, the engine, `just copy-plan`, the channel, the
    approval and the `unfinished` view are the real ones. Where the delivery itself is the
    subject, the hook's own copy is refused, so the resume this process drives is the one
    that copies; where the spikes ending is, the hook delivers and the spikes then end; and
    where the copy lands but no parent run is there to take the check-in, a resume retries
    the check-in alone.
    """
    import io

    import plan_fixture_source
    import plan_root_variable
    from github_board import BOARD, _GitHubFixture, _Operation, _serving_board
    from project_fixtures import FIXTURE_UNIT, budgeted, no_budgets, plan_repositories, reviewed

    from orchestrator import plan_store, spike_brief_budgets, unfinished
    from orchestrator.project_store import write_plan_project

    authoring = tmp_path / "authoring"
    authoring.mkdir()
    runs = tmp_path / "runs"
    session = "delivery-manager"
    monkeypatch.setenv(plan_root_variable.name(), str(authoring))
    monkeypatch.setenv(plan_fixture_source.ROOT_VARIABLE, str(tmp_path / "fixtures"))
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(runs))
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER_SESSION", session)
    monkeypatch.setenv("ONEPIPELINE_LAUNCHER", "codex")
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS", "0")
    script_paid_words(monkeypatch)
    choices = tmp_path / "choices.json"
    choices.write_text(
        json.dumps(
            {
                "Render exactly one spike-brief document": [
                    [
                        str(REPO_ROOT / ".venv/bin/python"),
                        str(Path(__file__).resolve()),
                        str(tmp_path),
                        "valid",
                    ]
                ]
            }
        )
    )
    monkeypatch.setenv("FAKE_BACKEND_RUN_ON_MARKER", str(choices))
    plan = {
        "schema_version": 3,
        "name": "preview",
        "goal": {"text": "Preview the measurements before they are spent"},
        "tasks": [
            {
                "id": "implement",
                "kind": "human",
                "task": "Implement the measured interface.",
                "unit": FIXTURE_UNIT,
            }
        ],
    }
    path = runs / "preview" / spike_brief.RECEIPT
    with _serving_board() as remote:
        for name, value in remote.items():
            monkeypatch.setenv(name, value)
        write_plan_project(authoring, plan, native_id="preview")
        budgeted("authoring", "preview", no_budgets(plan_repositories(plan)))
        reviewed("authoring:preview")
        client = plan_store.client()
        plan_store.sdk(client.project_create("authoring", "preview-spikes", "Six spike agents"))
        for row in _rows():
            plan_store.sdk(
                client.task_create(
                    "authoring",
                    "preview-spikes",
                    row["spike"],
                    metadata=["onepipeline.id=" + json.dumps(row["spike"])],
                    body="Measure the valid and rejected request behavior.",
                )
            )
        _holding_run("preview-spikes")
        if ending != "unsurfaced":
            _holding_run("preview")
        if ending in ["live", "late"]:
            # The hook's own copy is refused, so the resume below is the one that copies.
            _GitHubFixture.refused_operation = _Operation.CREATE_ISSUE
        writer = "preview-spike-brief"
        try:
            launched = flow(capsys, "launch", "authoring:preview", "preview", "preview-spikes")
            assert launched.returncode == 0 and "advisory:" not in launched.stderr, launched.stderr
            hook_log = runs / writer / "hooks" / "success.log"
            if ending == "unsurfaced":
                _wait_until(
                    lambda: hook_log.exists() and "spike brief advisory:" in hook_log.read_text(),
                    "the writer's ready hook reported nothing",
                )
                copied = spike_brief.read(path)
                assert copied is not None and copied["board_project"] is not None
                created = len(BOARD.created)
                retried = _resumed(capsys, "preview")
                # The parent's directory holds the receipt and no run, so the check-in still
                # has nowhere to go; the copy that landed is not made again.
                assert "preview/launch.json" in retried.stderr, retried.stderr
                assert len(BOARD.created) == created
                assert spike_brief.read(path) == copied
                return
            if ending == "ended":

                def delivered() -> bool:
                    current = spike_brief.read(path)
                    return current is not None and current["brief"] is not None

                _wait_until(delivered, "the writer's ready hook delivered nothing")
                assert _engine("stop", "preview-spikes").returncode == 0
            else:
                _wait_until(
                    lambda: hook_log.exists() and "spike brief advisory:" in hook_log.read_text(),
                    "the writer's ready hook reported nothing",
                )
                _GitHubFixture.refused_operation = None
                receipt = spike_brief.read(path)
                assert receipt is not None and receipt["brief"] is None
                assert receipt["board_project"] is None, hook_log.read_text()
            if ending == "late":
                assert _engine("stop", "preview-spikes").returncode == 0
            resumed = _resumed(capsys, "preview")
            receipt = spike_brief.read(path)
            assert receipt is not None
            assert "spike brief advisory:" not in resumed.stderr, resumed.stderr
            brief = receipt["brief"]
            assert brief is not None and brief["url"].startswith("https://")
            board = receipt["board_project"]
            assert board is not None and board.startswith("plans:")
            queue = json.loads(_engine("channel", "queue", "preview").stdout)
            check_ins = [row for row in queue["surfaces"] if row["kind"] == "check-in"]
            assert len(check_ins) == 1 and brief["url"] in check_ins[0]["message"]
            if ending == "late":
                # Completed after its spikes ended, the brief is still copied and linked, but
                # only in retrospect: no approval is offered and no item is left open.
                assert "retrospective" in check_ins[0]["message"]
                assert "approve-spikes" not in check_ins[0]["message"]
                assert spike_brief.items(session) == ([], [])
                created = len(BOARD.created)
                assert "advisory" not in flow(capsys, "resume", "preview").stderr
                assert len(BOARD.created) == created and spike_brief.read(path) == receipt
                # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] In-process: docstring.
                assert spike_brief.main(["authoring:preview"]) == 2
                assert "spikes have ended" in capsys.readouterr().err
                return
            assert "just approve-spikes authoring:preview" in check_ins[0]["message"]
            if ending == "live":
                # A delivery holding the receipt turns a second one away, writing nothing. The
                # lock is what a concurrent delivery holds; test_plan_journey.py drives that
                # concurrent delivery as a command.
                # llmlint: ignore[tests_mirror_real_usage] see the comment above this line
                with spike_brief.locked(path):
                    busy = flow(capsys, "resume", "preview")
                assert "delivery is already running" in busy.stderr, busy.stderr
                # A hook for another writer finds no receipt of its own here.
                monkeypatch.setenv("ONEPIPELINE_RUN_ID", "another-spike-brief")
                assert flow(capsys, "hook").stderr == ""
                monkeypatch.delenv("ONEPIPELINE_RUN_ID")
                assert spike_brief.read(path) == receipt
            # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] In-process; see docstring.
            approval = spike_brief.main(["authoring:preview"])
            if ending == "ended":
                # Once the spikes end, nothing an approval could save is left to save.
                assert approval == 2 and "spikes have ended" in capsys.readouterr().err
                assert spike_brief.items(session) == ([], [])
                unapproved = spike_brief.read(path)
                assert unapproved is not None and unapproved["approval"] is None
                return
            assert approval == 0
            updated = spike_brief.read(path)
            assert updated is not None and updated["approval"] is not None
            assert updated["approval"]["digest"] == brief["digest"]
            assert updated["approval"]["spikes_run"] == "preview-spikes"
            assert {**updated, "approval": None} == receipt
            approved = path.read_bytes()
            # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] In-process; see docstring.
            assert spike_brief.main(["authoring:preview"]) == 0
            assert path.read_bytes() == approved, "a repeated approval rewrote the receipt"
            # A resume, and the writer's own hook entry, after delivery change nothing.
            assert "advisory" not in flow(capsys, "resume", "preview").stderr
            monkeypatch.setenv("ONEPIPELINE_RUN_ID", writer)
            assert "advisory" not in flow(capsys, "hook").stderr
            monkeypatch.delenv("ONEPIPELINE_RUN_ID")
            assert path.read_bytes() == approved
            assert len(json.loads(_engine("channel", "queue", "preview").stdout)["surfaces"]) == 1
            # The delivery the budget reads is the one this flow recorded, through the pinned
            # onebudgetspec running the registered command and through its entry point here.
            monkeypatch.setenv("SPIKE_BRIEF_FLOWS", "preview")
            checked = subprocess.run(
                [
                    str(REPO_ROOT / ".venv/bin/onebudgetspec"),
                    "check",
                    "orchestrator/budgets.yaml",
                    "--id",
                    spike_brief_budgets.DELIVERY,
                    "--json",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            reported = json.loads(checked.stdout)["results"][0]
            assert reported["unit"] == "seconds" and "store publication" in reported["detail"]
            result = tmp_path / "delivery-result.json"
            monkeypatch.setenv("ONEBUDGETSPEC_RESULT", str(result))
            # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] In-process; see docstring.
            assert spike_brief_budgets.main(["--measure", spike_brief_budgets.DELIVERY]) == 0, (
                capsys.readouterr().err
            )
            measured = json.loads(result.read_text())
            # onebudgetspec reports the figure at its own precision; the detail is verbatim.
            assert measured["value"] == pytest.approx(reported["actual"])
            assert measured["detail"] == reported["detail"]
            # Approved and unchanged, the item is gone; changed content brings it back.
            assert spike_brief.items(session) == ([], [])
            held = [
                doc
                for doc in plan_store.read_documents("authoring:preview")
                if str(doc.qualified_id) == brief["document"]
            ]
            loader = tmp_path / "rerender-loader.json"
            loader.write_text(
                _engine(
                    "template",
                    "resolve",
                    "spike-brief",
                    "--template-root",
                    str(REPO_ROOT / "templates"),
                    "--json",
                ).stdout
            )
            plan_store.sdk(
                client.document_render(
                    str(held[0].qualified_id),
                    template_loader=str(loader),
                    answers={
                        "overview": "Measure the revised interfaces before choosing one.",
                        "spikes": [
                            {"spike": row["spike"], "question": row["question"], "use": row["use"]}
                            for row in _rows()
                        ],
                    },
                )
            )
            rows, diagnostics = spike_brief.items(session)
            assert [row["url"] for row in rows] == [brief["url"]]
            assert "spike brief approval invalidated by changed content" in diagnostics
            out, err = io.StringIO(), io.StringIO()
            # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] In-process; see docstring.
            unfinished.main(["--session", session, "--json"], out=out, err=err)
            assert json.loads(out.getvalue())["spike_briefs"] == rows
            out, err = io.StringIO(), io.StringIO()
            # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] In-process; see docstring.
            unfinished.main(["--session", session], out=out, err=err)
            assert "spike briefs: optional user approval (advisory)" in out.getvalue()
            assert "just approve-spikes authoring:preview" in out.getvalue()
            assert "approval invalidated" in err.getvalue()
            changed = flow(capsys, "resume", "preview")
            assert "surfaced brief content changed" in changed.stderr, changed.stderr
            # llmlint: ignore[e2e_not_mocked, tests_mirror_real_usage] In-process; see docstring.
            assert spike_brief.main(["authoring:preview"]) == 2
            assert "brief content changed" in capsys.readouterr().err
            if os.geteuid() != 0:
                # A spikes run whose status cannot be read keeps the item, and says why.
                launch = runs / "preview-spikes" / "launch.json"
                mode = launch.stat().st_mode & 0o777
                try:
                    launch.chmod(0)
                    rows, diagnostics = spike_brief.items(session)
                finally:
                    launch.chmod(mode)
                assert [row["url"] for row in rows] == [brief["url"]]
                assert any("spike brief unresolved" in line for line in diagnostics)
            # The spikes ending retires the item without any approval.
            assert _engine("stop", "preview-spikes").returncode == 0
            assert spike_brief.items(session)[0] == []
        finally:
            _GitHubFixture.refused_operation = None
            for run in ["preview", "preview-spikes", writer]:
                if (runs / run / "launch.json").exists():
                    _engine("stop", run)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses checkout directory permissions")
def test_ready_hook_unenterable_checkout_is_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    checkout = tmp_path / "checkout"
    script = checkout / "scripts/spike-brief-ready.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts/spike-brief-ready.sh", script)
    monkeypatch.chdir(script.parent)
    mode = checkout.stat().st_mode & 0o777
    try:
        checkout.chmod(0)
        result = subprocess.run(
            ["/bin/bash", "./spike-brief-ready.sh"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        assert "cannot enter" in result.stderr, result.stderr
        assert "restore the checkout" in result.stderr
    finally:
        checkout.chmod(mode)


if __name__ == "__main__":
    import sys

    write_invalid_document(Path(sys.argv[1]), sys.argv[2])
