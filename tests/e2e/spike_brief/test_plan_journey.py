"""Real planning stages and ordinary board copy, with only paid words and GitHub doubled."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier, Event

import plan_root_variable
import pytest
from github_board import (
    BOARD,
    CONFIGURED_PROJECT_NUMBER,
    STATUS_OPTIONS,
    _GitHubFixture,
    _GraphQLRequest,
    _Operation,
    _served,
    _serving_board,
)
from materialized import checkout
from project_fixtures import helper, no_budgets
from scratch_identity import PLANNING_FLOW_ORIGIN, seeded
from waits import deadline as e2e_deadline
from waits import timeout as e2e_timeout

from orchestrator import run_reading, spike_brief
from orchestrator.root import REPO_ROOT

# What the recipes these journeys run leave in the temporary directory, by design or
# from a published tool they run, is this module's own and goes with it
# (`tests/short_state.py`).
pytestmark = [
    pytest.mark.xdist_group("spike-brief-planning"),
    pytest.mark.usefixtures("module_tmpdir"),
]


def wait(path: Path, process: subprocess.Popen[str], log: Path) -> None:
    deadline = e2e_deadline(300)
    while not path.exists():
        assert process.poll() is None, log.read_text()[-5000:]
        assert time.monotonic() < deadline, f"rendezvous missing: {path}\n{log.read_text()[-5000:]}"
        time.sleep(0.05)


def just(environment: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *args],
        cwd=environment["BRIEF_CHECKOUT"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def prepare(
    tmp_path: Path, name: str, remote: dict[str, str], *, early: bool, workspace: Path
) -> tuple[dict[str, str], Path, Path, Path, str]:
    identity = seeded(tmp_path / "identity", origin=PLANNING_FLOW_ORIGIN, visibility="public")
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    scratch = tmp_path / "actions"
    scratch.mkdir()
    if early:
        (scratch / "hold-spikes").touch()
    project = name + "-plan"
    source = tmp_path / "budgets-answers.json"
    source.write_text(json.dumps(no_budgets([PLANNING_FLOW_ORIGIN])))
    actions = Path(__file__).parent / "actions.py"
    base = [str(workspace / ".venv/bin/python"), str(actions)]
    commands: dict[str, list[list[str]]] = {
        "AUTHOR-BRIEF-FIXTURE-" + name: [
            base
            + [
                "draft",
                project,
                name,
                str(scratch),
                "--repository",
                PLANNING_FLOW_ORIGIN,
                "--budget-answers",
                str(source),
            ]
        ],
        "Read local authoring project authoring:" + project: [
            base + ["brief", project, name, str(scratch)]
        ],
        "Finalize this plan from its spikes": [base + ["finalize", project, name, str(scratch)]],
        "Write the design document a person reviews the plan": [
            base + ["design", project, name, str(scratch)]
        ],
    }
    for index in range(6):
        node = f"spike-{project}-agent-{index}"
        commands[f"Measure and compare the {node} interface."] = [
            base + ["spike", project, name, str(scratch), "--node", node]
        ]
    choices = tmp_path / "choices.json"
    choices.write_text(json.dumps(commands))
    brief = tmp_path / "brief.md"
    brief.write_text(
        f"## What\n\nAUTHOR-BRIEF-FIXTURE-{name}\n\n"
        f"Plan project: authoring:{project}\n\n"
        "## Why\n\nMeasured interfaces prevent unnecessary work.\n\n"
        "## Acceptance criteria\n\n- The six measurements inform the implementation.\n"
    )
    environment = {
        **os.environ,
        **identity.environment,
        **remote,
        "BRIEF_CHECKOUT": str(workspace),
        "PYTHONPATH": os.pathsep.join([str(workspace), str(REPO_ROOT / "tests/e2e")]),
        "UV_NO_SYNC": "1",
        "ONEVCS_HOME": str(identity.home),
        "ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs"),
        "ONEPIPELINE_LAUNCHER_SESSION": name,
        "ONEPIPELINE_LAUNCHER": "codex",
        # Hang guards the scripted stages read inside their dispatch, named in the `FAKE_`
        # family `scripts/dispatch-env.sh` lets a dispatch keep: any other name is dropped.
        # How long the scripted spike's question waits for its answer.
        "FAKE_BRIEF_ASK_WINDOW": str(int(e2e_timeout(300))),
        # How long a scripted stage waits for the journey to release it.
        "FAKE_BRIEF_RELEASE_WINDOW": str(int(e2e_timeout(300))),
        plan_root_variable.name(): str(authoring),
        plan_root_variable.plugin_name(): "local-md",
        # The engine, store, graph and harness run for real; the adapter scripts paid words.
        # llmlint: ignore-block[e2e_not_mocked] Only the paid provider is doubled.
        "ONEAGENTGRAPH_ONEHARNESS_BIN": str(Path(__file__).parent / "backend.py"),
        "ONEHARNESS_HISTORY_DIR": str(tmp_path / "history"),
        "REAL_ONEHARNESS_BIN": str(workspace / ".venv/bin/oneharness"),
        "ONEHARNESS_BIN_CODEX": str(helper("fake_codex.py")),
        "FAKE_CODEX_ANSWERS": json.dumps([json.dumps({"passes": True, "findings": []})]),
        "FAKE_BACKEND_SOURCE": str(helper("fake_backend.py")),
        "FAKE_BACKEND_RUN_ON_MARKER": str(choices),
        "FAKE_BACKEND_PROMPT_LOG": str(tmp_path / "prompts.jsonl"),
        "PATH": str(helper("no-paid-provider")) + os.pathsep + os.environ["PATH"],
        # llmlint: ignore-end[e2e_not_mocked]
        "ONETASKGRAPH_SOURCES__PLANS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS": "0",
    }
    return environment, brief, scratch, tmp_path / "plan.log", project


def prove_whole_project_publication(environment: dict[str, str], project: str) -> None:
    inspected = subprocess.run(
        [
            str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/python"),
            "-c",
            "import json,sys; from orchestrator import plan_store; "
            "source=plan_store.QualifiedProjectId(sys.argv[1]); "
            "board=plan_store.project_record(source)['metadata'][plan_store.COPIES_KEY]['plans']; "
            "tasks=plan_store.read_tasks(board); docs=plan_store.read_documents(board); "
            "drafted=plan_store.read_documents(source); "
            "print(json.dumps({'board':board,'tasks':[str(t.qualified_id) for t in tasks],"
            "'documents':[{'id':str(d.qualified_id),'origin':d.metadata.get(plan_store.ORIGIN_KEY),"
            "'content':d.content,"
            "'template':d.metadata.get('onetaskgraph.template',{}).get('template')} "
            "for d in docs],"
            "'links':[d.metadata.get(plan_store.COPIES_KEY,{}).get('plans') for d in drafted]}))",
            "authoring:" + project,
        ],
        cwd=environment["BRIEF_CHECKOUT"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert inspected.returncode == 0, inspected.stderr
    held = json.loads(inspected.stdout)
    assert len(held["tasks"]) == 20 and len(set(held["tasks"])) == 20
    documents = held["documents"]
    # A copy out of the private authoring source carries no origin: the store keeps the
    # correspondence on the authoring side, each drafted document linking the copy it reached.
    assert len(documents) == 8 and {doc["origin"] for doc in documents} == {None}
    assert sorted(held["links"]) == sorted(doc["id"] for doc in documents)
    briefs = [doc for doc in documents if doc["template"] == spike_brief.TEMPLATE_REFERENCE]
    assert len(briefs) == 1 and len(briefs[0]["content"].split()) > 250
    assert len([line for line in briefs[0]["content"].splitlines() if line.startswith("## ")]) == 6
    assert len([doc for doc in documents if doc["template"] == "onepipeline:design-doc"]) == 1
    assert len([doc for doc in documents if doc["template"] == "onepipeline:spike-report"]) == 6


def prove_unavailable_status_is_advisory(environment: dict[str, str], name: str, url: str) -> None:
    if os.geteuid() == 0:
        return  # The separate permission journeys state their root skip explicitly.
    before = just(environment, "unfinished", "--session", name, "--json")
    launch = Path(environment["ONEPIPELINE_RUNS_DIR"]) / (name + "-spikes") / "launch.json"
    mode = launch.stat().st_mode & 0o777
    try:
        launch.chmod(0)
        status = engine_command(environment, "status", name + "-spikes", "--json")
        assert status.returncode != 0 and "Permission denied" in status.stderr
        viewed = just(environment, "unfinished", "--session", name, "--json")
        assert viewed.returncode == before.returncode, viewed.stderr + viewed.stdout
        assert "spike brief unresolved" in viewed.stderr
        assert json.loads(viewed.stdout)["spike_briefs"][0]["url"] == url
    finally:
        launch.chmod(mode)


def prove_stop_guard_keeps_optional_approval_advisory(
    environment: dict[str, str], name: str, scratch: Path
) -> None:
    wait_record(scratch / "question-reached")
    deadline = e2e_deadline(60)
    while True:
        queue = engine_command(environment, "channel", "queue", name + "-spikes")
        surfaces = json.loads(queue.stdout)["surfaces"]
        questions = [row for row in surfaces if row.get("blocking")]
        if questions:
            break
        assert time.monotonic() < deadline, queue.stdout
        time.sleep(0.05)
    handed_question = engine_command(environment, "next", name + "-spikes")
    assert handed_question.returncode == 0, handed_question.stderr
    assert (
        json.loads(handed_question.stdout)["surface"]["correlation"] == questions[0]["correlation"]
    )
    answer = subprocess.run(
        [
            str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/onepipeline"),
            "reply",
            name + "-spikes",
            "--correlation",
            questions[0]["correlation"],
        ],
        env=environment,
        cwd=environment["BRIEF_CHECKOUT"],
        input=json.dumps({"version": 3, "completion": True, "reason": "Continue the measurement."}),
        capture_output=True,
        text=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert answer.returncode == 0, answer.stderr
    wait_record(scratch / "question-answered")
    for run in [name, name + "-spikes", name + "-spike-brief"]:
        for _ in range(10):
            handed = engine_command(environment, "next", run)
            assert handed.returncode == 0, handed.stderr
            surface = json.loads(handed.stdout)["surface"]
            if surface is None:
                break
            assert not surface["blocking"], str(surface)
        else:
            raise AssertionError("the fixture queued more than ten advisory messages")
    for run in [name, name + "-spikes", name + "-spike-brief"]:
        result = engine_command(
            environment,
            "unwatched",
            "--session",
            name,
            "--acknowledge",
            run,
            "--reason",
            "The fixture cleared required work while leaving optional approval open",
        )
        assert result.returncode == 0, result.stderr
    # Every spike but the held agent-5 settles and keeps its branch; read the list once all
    # five are kept, so none settling a moment later is left unacknowledged.
    deadline = e2e_deadline(120)
    while True:
        kept = just(environment, "unpublished", "--session", name, "--format", "json")
        rows = json.loads(kept.stdout)["rows"]
        settled = {
            row["node"].rsplit("-", 1)[-1] for row in rows if "-agent-" in (row["node"] or "")
        }
        if settled >= {"0", "1", "2", "3", "4"}:
            break
        assert time.monotonic() < deadline, kept.stdout
        time.sleep(0.1)
    for row in rows:
        result = just(
            environment,
            "unpublished",
            "--acknowledge",
            row["branch"],
            "--reason",
            "Keep completed measurement evidence",
        )
        assert result.returncode in [0, 7] and "acknowledged " + row["branch"] in result.stdout, (
            result.stderr + result.stdout
        )
    watch_log = scratch / "guard-flow-watch.log"
    with watch_log.open("w") as output:
        watcher = subprocess.Popen(
            [
                str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/onepipeline"),
                "watch",
                "--flow",
                name,
                "--timeout",
                # Outlasts the guarded wait below, whose bound scales the same way.
                str(int(e2e_timeout(60))),
            ],
            env=environment,
            cwd=environment["BRIEF_CHECKOUT"],
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
        )
    try:
        deadline = e2e_deadline(30)
        while True:
            view = just(environment, "unfinished", "--session", name, "--json")
            if view.returncode == 0:
                break
            assert watcher.poll() is None, watch_log.read_text()
            assert time.monotonic() < deadline, view.stderr + view.stdout
            time.sleep(0.05)
        assert "spike_briefs" in json.loads(view.stdout)
        # The `Stop` hook `.claude/settings.json` registers, with its session named.
        state = environment.get("XDG_STATE_HOME") or str(
            Path(environment["HOME"]) / ".local" / "state"
        )
        guarded = engine_command(
            environment,
            "stop-guard",
            "--session",
            name,
            "--unpublished",
            "--unpublished-acknowledgements",
            str(Path(state) / "ai-orchestrator" / "stop-unfinished" / "acknowledged"),
            "--unpublished-pr-author-graph",
            str(Path(environment["BRIEF_CHECKOUT"]) / "graphs" / "pr-author.yaml"),
        )
        assert guarded.returncode == 0 and json.loads(guarded.stdout)["verdict"] == "none", (
            guarded.stdout
        )
    finally:
        if watcher.poll() is None:
            watcher.terminate()
        watcher.wait(timeout=e2e_timeout(30))


@contextmanager
def changed_brief(environment: dict[str, str], path: Path, scratch: Path) -> Iterator[None]:
    """Regenerate the surfaced brief with a changed overview, and restore it on the way out."""
    receipt = json.loads(path.read_text())
    document_id = receipt["brief"]["document"]
    held = just(environment, "plans", "document", "answers", document_id, "--json")
    assert held.returncode == 0, held.stderr
    original = json.loads(held.stdout)
    answers_file = scratch / "changed-brief.json"
    loader = scratch / "spike-brief-loader.json"

    def rendered(answers: dict[str, object]) -> None:
        answers_file.write_text(json.dumps(answers))
        result = just(
            environment,
            "plans",
            "document",
            "render",
            document_id,
            "--template-loader",
            str(loader),
            "--answers",
            str(answers_file),
            "--no-interactive",
        )
        assert result.returncode == 0, result.stderr

    try:
        rendered(
            {
                **original,
                "overview": original["overview"] + " Recheck the rejected request evidence.",
            }
        )
        yield
    finally:
        rendered(original)


def prove_changed_content_invalidates_approval(
    environment: dict[str, str], path: Path, scratch: Path
) -> None:
    receipt = json.loads(path.read_text())
    with changed_brief(environment, path, scratch):
        view = just(environment, "unfinished", "--session", receipt["manager_session"], "--json")
        assert "invalidated by changed content" in view.stderr
        assert "spike_briefs" in json.loads(view.stdout)
        assert just(environment, "approve-spikes", receipt["plan_project"]).returncode == 2
        resumed = subprocess.run(
            [
                str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/python"),
                "-m",
                "orchestrator.spike_brief_flow",
                "resume",
                receipt["parent_run"],
            ],
            cwd=environment["BRIEF_CHECKOUT"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=e2e_timeout(30),
            check=False,
        )
        assert resumed.returncode == 0 and "content changed" in resumed.stderr
        assert json.loads(path.read_text()) == receipt


def record_budget_history(environment: dict[str, str], parent: str, *, delivered: bool) -> None:
    root = Path(environment["ONEPIPELINE_RUNS_DIR"])
    output = Path(os.environ.get("BUDGET_TELEMETRY_DIR", str(root / "telemetry"))) / parent
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        root / parent / spike_brief.DELIVERY_HISTORY, output / spike_brief.DELIVERY_HISTORY
    )
    pointers = root / (parent + "-spike-brief") / "oneharness-sessions.jsonl"
    saved: list[dict[str, object]] = []
    for index, line in enumerate(pointers.read_text().splitlines()):
        pointer = json.loads(line)
        history = output / f"model-{index}.jsonl"
        shutil.copy2(str(pointer["history_file"]), history)
        pointer["history_file"] = str(history.resolve())
        saved.append(pointer)
    (output / "oneharness-sessions.jsonl").write_text(
        "\n".join(json.dumps(pointer) for pointer in saved) + "\n"
    )
    measures = [("spike-brief-model-tokens", "tokens")]
    if delivered:
        measures.append(("spike-brief-delivery-seconds", "seconds"))
    for measure, unit in measures:
        result = subprocess.run(
            [
                str(REPO_ROOT / ".venv/bin/onebudgetspec"),
                "check",
                "orchestrator/budgets.yaml",
                "--id",
                measure,
                "--json",
            ],
            cwd=environment["BRIEF_CHECKOUT"],
            env={**environment, "SPIKE_BRIEF_FLOWS": parent},
            capture_output=True,
            text=True,
            timeout=e2e_timeout(30),
            check=False,
        )
        report = json.loads(result.stdout)
        row = report["results"][0]
        assert row["unit"] == unit and row["actual"] is not None, result.stderr + result.stdout
        assert row["detail"], result.stdout
        if unit == "tokens":
            assert "codex:primary" in row["detail"]
            assert "; codex;" not in row["detail"]
            assert "input 4 + output 1" in row["detail"]
            assert "worker" in row["detail"] and "judge" in row["detail"]
        else:
            assert all(
                phase in row["detail"]
                for phase in ["writer", "store publication", "channel delivery"]
            )
        (output / (measure + ".json")).write_text(result.stdout)


def drive_flow(tmp_path: Path, early: bool, remote: dict[str, str], writers: Barrier) -> None:
    tmp_path.mkdir()
    name = "brief-journey-" + ("early" if early else "late")
    environment, brief, scratch, log, project = prepare(
        tmp_path, name, remote, early=early, workspace=checkout(tmp_path / "checkout")
    )
    if early:
        (scratch / "ask-spikes").touch()
    with log.open("w") as output:
        process = subprocess.Popen(
            ["just", "plan", str(brief), "--name", name],
            cwd=environment["BRIEF_CHECKOUT"],
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    try:
        wait(scratch / "writer-reached", process, log)
        writers.wait(timeout=e2e_timeout(120))
        if early:
            (scratch / "writer-release").touch()
            receipt_path = Path(environment["ONEPIPELINE_RUNS_DIR"]) / name / spike_brief.RECEIPT
            deadline = e2e_deadline(300)
            receipt: spike_brief.Receipt | None = None
            while receipt is None or receipt["brief"] is None:
                hook_log = (
                    receipt_path.parent.parent / (name + "-spike-brief") / "hooks/success.log"
                )
                if hook_log.exists():
                    assert "spike brief advisory:" not in hook_log.read_text(), hook_log.read_text()
                assert process.poll() is None, log.read_text()[-5000:]
                assert time.monotonic() < deadline, log.read_text()[-5000:]
                receipt = spike_brief.validate(json.loads(receipt_path.read_text()))
                time.sleep(0.05)
            assert receipt["board_project"] is not None and receipt["brief"] is not None
            assert receipt["board_project"].startswith("plans:")
            assert receipt["brief"]["url"].startswith("https://")
            for _ in range(12):
                view = just(environment, "unfinished", "--session", name, "--json")
                assert view.stdout.strip(), view.stderr
                assert json.loads(view.stdout)["spike_briefs"][0]["url"] == receipt["brief"]["url"]
            text = just(environment, "unfinished", "--session", name)
            assert "spike briefs: optional user approval (advisory)" in text.stdout
            assert "just approve-spikes authoring:" + project in text.stdout
            foreign = just(environment, "unfinished", "--session", "foreign", "--json")
            assert "spike_briefs" not in json.loads(foreign.stdout)
            design_refused = just(environment, "approve-design", "authoring:" + project)
            assert design_refused.returncode != 0
            refused = just(
                {**environment, "ONEPIPELINE_LAUNCHER_SESSION": "foreign"},
                "approve-spikes",
                "authoring:" + project,
            )
            assert refused.returncode == 2
            queue = subprocess.run(
                [str(REPO_ROOT / ".venv/bin/onepipeline"), "channel", "queue", name],
                env=environment,
                capture_output=True,
                text=True,
                timeout=e2e_timeout(30),
                check=True,
            )
            surfaces = json.loads(queue.stdout)["surfaces"]
            check_ins = [surface for surface in surfaces if surface["kind"] == "check-in"]
            assert len(check_ins) == 1 and receipt["brief"]["url"] in check_ins[0]["message"]
            before_receipt = receipt_path.read_bytes()
            wrong_owner = subprocess.run(
                [
                    str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/python"),
                    "-m",
                    "orchestrator.spike_brief_flow",
                    "launch",
                    "authoring:" + project,
                    name,
                    name + "-other-spikes",
                ],
                cwd=environment["BRIEF_CHECKOUT"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=e2e_timeout(30),
                check=False,
            )
            assert wrong_owner.returncode == 0 and "another flow" in wrong_owner.stderr
            assert "after repairing it" in wrong_owner.stderr
            assert receipt_path.read_bytes() == before_receipt
            brief_url = receipt["brief"]["url"]
            prove_stop_guard_keeps_optional_approval_advisory(environment, name, scratch)
            receipt = spike_brief.validate(json.loads(receipt_path.read_text()))
            approved = just(environment, "approve-spikes", "authoring:" + project)
            assert approved.returncode == 0, approved.stderr
            recorded_approval = json.loads(receipt_path.read_text())["approval"]
            assert just(environment, "approve-spikes", "authoring:" + project).returncode == 0
            assert json.loads(receipt_path.read_text())["approval"] == recorded_approval
            approved_view = just(environment, "unfinished", "--session", name, "--json")
            assert "spike_briefs" not in json.loads(approved_view.stdout)
            prove_changed_content_invalidates_approval(environment, receipt_path, scratch)
            (scratch / "spikes-release").touch()
        wait(scratch / "finalize-reached", process, log)
        wait(scratch / "design-reached", process, log)
        if early:
            # Held at its design stage, the flow no longer reads the ended spikes run, so its
            # status can be made unreadable without failing the flow; the item stays rather
            # than reading as cleared.
            # A changed brief no longer matches its approval, so the item is left to the
            # spikes' status to retire, which here cannot be read.
            with changed_brief(environment, receipt_path, scratch):
                prove_unavailable_status_is_advisory(environment, name, brief_url)
        if not early:
            assert not (scratch / "writer-release").exists()
            (scratch / "writer-release").touch()
            wait(scratch / "brief-written", process, log)
            # The spikes have ended, so the delivery is retrospective: still copied and
            # linked, while the flow is held at its design stage.
            late = wait_delivered(Path(environment["ONEPIPELINE_RUNS_DIR"]) / name)
            assert_retrospective_check_in(environment, name, late)
        (scratch / "design-release").touch()
        assert process.wait(timeout=e2e_timeout(120)) == 0, log.read_text()[-8000:]
        receipt = json.loads(
            (Path(environment["ONEPIPELINE_RUNS_DIR"]) / name / spike_brief.RECEIPT).read_text()
        )
        assert receipt["board_project"] is not None and receipt["brief"] is not None
        source = json.loads((scratch / "source-read.json").read_text())
        assert len(source["tasks"]) == 20 and len(source["spikes"]) == 6
        prove_whole_project_publication(environment, project)
        record_budget_history(environment, name, delivered=True)
    finally:
        for released in ["writer-release", "spikes-release", "design-release"]:
            (scratch / released).touch()
        for run in [
            name,
            name + "-spikes",
            name + "-finalize",
            name + "-design",
            name + "-spike-brief",
        ]:
            if (Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "launch.json").exists():
                subprocess.run(
                    [str(REPO_ROOT / ".venv/bin/onepipeline"), "stop", run],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=e2e_timeout(60),
                    check=False,
                )
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=e2e_timeout(30))


def test_two_real_planning_flows_keep_stages_independent_and_use_ordinary_copy(
    tmp_path: Path,
) -> None:
    with _serving_board() as remote, ThreadPoolExecutor(max_workers=2) as workers:
        writers = Barrier(2)
        futures = [
            workers.submit(drive_flow, tmp_path / name, early, remote, writers)
            for name, early in [("writer-first", True), ("spikes-first", False)]
        ]
        for future in futures:
            future.result()


def engine_command(environment: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/onepipeline"), *args],
        cwd=environment["BRIEF_CHECKOUT"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def wait_record(path: Path) -> None:
    deadline = e2e_deadline(180)
    while not path.exists():
        assert time.monotonic() < deadline, f"missing rendezvous: {path}"
        time.sleep(0.05)


def wait_content(path: Path, text: str) -> None:
    deadline = e2e_deadline(180)
    while not path.exists() or text not in path.read_text():
        assert time.monotonic() < deadline, f"missing output {text!r}: {path}"
        time.sleep(0.05)


def wait_ended(environment: dict[str, str], run: str) -> None:
    deadline = e2e_deadline(180)
    while True:
        status = engine_command(environment, "status", run, "--json")
        assert status.returncode == 0, status.stderr
        reading = run_reading.decide(status.stdout, run_reading.RunId(run))
        if reading.decision == run_reading.Decision.ENDED:
            return
        assert time.monotonic() < deadline, status.stdout
        time.sleep(0.05)


def wait_delivered(parent: Path) -> spike_brief.Receipt:
    """The receipt once its delivery recorded a surfaced brief."""
    deadline = e2e_deadline(300)
    while True:
        # The store is the flow's, so the test process holds the receipt to its schema only.
        receipt = spike_brief.validate(json.loads((parent / spike_brief.RECEIPT).read_text()))
        if receipt["brief"] is not None:
            return receipt
        assert time.monotonic() < deadline, f"no brief delivered under {parent}"
        time.sleep(0.05)


def assert_retrospective_check_in(
    environment: dict[str, str], parent: str, receipt: spike_brief.Receipt
) -> None:
    """One check-in carrying the board link, offering no approval and leaving no item."""
    brief = receipt["brief"]
    assert brief is not None and brief["url"].startswith("https://")
    assert receipt["board_project"] is not None and receipt["board_project"].startswith("plans:")
    queue = engine_command(environment, "channel", "queue", parent)
    assert queue.returncode == 0, queue.stderr
    check_ins = [row for row in json.loads(queue.stdout)["surfaces"] if row["kind"] == "check-in"]
    assert len(check_ins) == 1 and brief["url"] in check_ins[0]["message"]
    assert "retrospective" in check_ins[0]["message"]
    assert "Optional user approval" not in check_ins[0]["message"]
    assert "approve-spikes" not in check_ins[0]["message"]
    view = just(environment, "unfinished", "--session", parent, "--json")
    assert "spike_briefs" not in json.loads(view.stdout)


@pytest.mark.parametrize("early", [True, False], ids=["partial-copy", "late-retrospective"])
def test_real_detached_flow_resumes_at_the_brief_boundary(tmp_path: Path, early: bool) -> None:
    entered, released = Event(), Event()
    resume_copy = Event()

    class ResumeBoard(_GitHubFixture):
        def do_POST(self) -> None:
            if resume_copy.is_set():
                entered.set()
                assert released.wait(timeout=e2e_timeout(120)), "resume's copy was not released"
            super().do_POST()

    name = "brief-resume-" + ("early" if early else "late")
    with _served(ResumeBoard, BOARD, STATUS_OPTIONS, CONFIGURED_PROJECT_NUMBER) as endpoint:
        remote = {
            "GH_PROJECTS_TOKEN": "fixture-token",
            "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": endpoint,
        }
        environment, brief, scratch, log, project = prepare(
            tmp_path, name, remote, early=early, workspace=checkout(tmp_path / "checkout")
        )
        process: subprocess.Popen[str] | None = None
        try:
            drafted = just(environment, "plan", str(brief), "--name", name, "--detach")
            assert drafted.returncode == 0, drafted.stderr
            wait_record(scratch / "draft-written")
            wait_ended(environment, name)
            spikes = just(
                environment, "plan", str(brief), "--name", name, "--resume", "spikes", "--detach"
            )
            assert spikes.returncode == 0, spikes.stderr
            wait_record(scratch / "writer-reached")
            receipt_path = Path(environment["ONEPIPELINE_RUNS_DIR"]) / name / spike_brief.RECEIPT
            if early:
                _GitHubFixture.refused_operation = _Operation.CREATE_ISSUE
                _GitHubFixture.refused_after = 1
                (scratch / "writer-release").touch()
                wait_ended(environment, name + "-spike-brief")
                hook = receipt_path.parent.parent / (name + "-spike-brief") / "hooks/success.log"
                wait_content(hook, "advisory")
                assert "copy-plan" in hook.read_text() and "advisory" in hook.read_text()
                assert json.loads(receipt_path.read_text())["board_project"] is None
                assert BOARD.created, "the refused ordinary copy produced no partial publication"
                _GitHubFixture.refused_operation = None
                resume_copy.set()
            else:
                wait_ended(environment, name + "-spikes")
            with log.open("w") as output:
                process = subprocess.Popen(
                    ["just", "plan", str(brief), "--name", name, "--resume", "spikes"],
                    cwd=environment["BRIEF_CHECKOUT"],
                    env=environment,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            if early:
                assert entered.wait(timeout=e2e_timeout(120)), log.read_text()
                concurrent = subprocess.run(
                    [
                        str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/python"),
                        "-m",
                        "orchestrator.spike_brief_flow",
                        "resume",
                        name,
                    ],
                    cwd=environment["BRIEF_CHECKOUT"],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=e2e_timeout(30),
                    check=False,
                )
                assert concurrent.returncode == 0 and "already running" in concurrent.stderr
                unsurfaced = just(environment, "approve-spikes", "authoring:" + project)
                assert unsurfaced.returncode == 2 and "surfaced brief" in unsurfaced.stderr
                assert not released.is_set()
                (scratch / "spikes-release").touch()
                wait_ended(environment, name + "-spikes")
                released.set()
            wait(scratch / "design-reached", process, log)
            if not early:
                (scratch / "writer-release").touch()
                wait_record(scratch / "brief-written")
                wait_ended(environment, name + "-spike-brief")
                wait_delivered(receipt_path.parent)
            (scratch / "design-release").touch()
            assert process.wait(timeout=e2e_timeout(120)) == 0, log.read_text()[-5000:]
            prove_whole_project_publication(environment, project)
            receipt = json.loads(receipt_path.read_text())
            assert receipt["approval"] is None
            # Partially copied before the spikes ended, or written only after they ended, the
            # brief reaches the board once and its one check-in is retrospective.
            assert_retrospective_check_in(environment, name, spike_brief.validate(receipt))
            view = just(environment, "unfinished", "--session", name, "--json")
            assert "spike_briefs" not in json.loads(view.stdout)
            assert (
                len(
                    list(Path(environment["ONEPIPELINE_RUNS_DIR"]).glob("*spike-brief/launch.json"))
                )
                == 1
            )
        finally:
            released.set()
            _GitHubFixture.refused_operation = None
            for artifact in ["writer-release", "spikes-release", "design-release"]:
                (scratch / artifact).touch()
            for run in [
                name,
                name + "-spikes",
                name + "-finalize",
                name + "-design",
                name + "-spike-brief",
            ]:
                if (Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "launch.json").exists():
                    engine_command(environment, "stop", run)
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=e2e_timeout(30))


def fixture_action(
    environment: dict[str, str], verb: str, project: str, name: str, scratch: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/python"),
            str(Path(__file__).parent / "actions.py"),
            verb,
            project,
            name,
            str(scratch),
        ],
        cwd=environment["BRIEF_CHECKOUT"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(30),
        check=False,
    )


def brief_resume(environment: dict[str, str], name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/python"),
            "-m",
            "orchestrator.spike_brief_flow",
            "resume",
            name,
        ],
        cwd=environment["BRIEF_CHECKOUT"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(120),
        check=False,
    )


#: A source of the decoy plan's own, declared nowhere private, so a copy out of it records the
#: decoy's origin on the board item it reaches.
DECOY_SOURCE = "decoy"


def rebind(environment: dict[str, str], tmp_path: Path, plan: str, *, decoy: bool = False) -> str:
    """Copy onto the board with the store's `--match-by title`, answering the project reached.

    With ``decoy``, a plan of :data:`DECOY_SOURCE` titled as ``plan`` is created and copied, so
    it takes over ``plan``'s board project and stamps its own origin there; otherwise ``plan``
    itself is copied, taking that project back.
    """
    store = str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/onetaskgraph")
    root = tmp_path / DECOY_SOURCE
    root.mkdir(exist_ok=True)
    scoped = {
        **environment,
        f"ONETASKGRAPH_SOURCES__{DECOY_SOURCE.upper()}__PLUGIN": "local-md",
        f"ONETASKGRAPH_SOURCES__{DECOY_SOURCE.upper()}__CONFIG__ROOT": str(root),
    }

    def run(*arguments: str) -> str:
        done = subprocess.run(
            [store, "--json", *arguments],
            cwd=environment["BRIEF_CHECKOUT"],
            env=scoped,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout

    copied = plan
    if decoy:
        title = json.loads(run("project", "show", plan))["items"][0]["item"]["title"]
        run("project", "create", DECOY_SOURCE, "--id", "another-plan", "--title", title)
        copied = f"{DECOY_SOURCE}:another-plan"
    report = json.loads(run("project", "copy", copied, "--to", "plans", "--match-by", "title"))
    (reached,) = [str(item["destination"]) for item in report["items"] if item["source"] == copied]
    return reached


@pytest.mark.parametrize(
    "fault", ["initialize", "launch", "writer", "spike", "channel", "origin", "url"]
)
def test_real_detached_planning_keeps_brief_failures_advisory(tmp_path: Path, fault: str) -> None:
    name = "brief-fault-" + fault
    broken_board = Event()
    if fault == "url":
        broken_board.set()
    receipt_path: Path | None = None

    class CloseoutBoard(_GitHubFixture):
        def _answer(self, request: _GraphQLRequest) -> dict[str, object]:
            recorded = (
                None
                if receipt_path is None or not receipt_path.exists()
                else json.loads(receipt_path.read_text())["board_project"]
            )
            answered = super()._answer(request)
            if not broken_board.is_set() or recorded is None:
                return answered

            def corrupt(value: object, key: str = "") -> object:
                match value:
                    case dict():
                        return {name: corrupt(child, name) for name, child in value.items()}
                    case list():
                        return [corrupt(child) for child in value]
                    case str():
                        if key == "url":
                            return "http://github.com/unreadable"
                        return value
                    case _:
                        return value

            return {key: corrupt(value, key) for key, value in answered.items()}

    with _served(CloseoutBoard, BOARD, STATUS_OPTIONS, CONFIGURED_PROJECT_NUMBER) as endpoint:
        remote = {
            "GH_PROJECTS_TOKEN": "fixture-token",
            "ONETASKGRAPH_SOURCES__PLANS__CONFIG__ENDPOINT": endpoint,
        }
        environment, brief, scratch, log, project = prepare(
            tmp_path, name, remote, early=True, workspace=checkout(tmp_path / "checkout")
        )
        channel_modes: dict[Path, int] = {}
        try:
            drafted = just(environment, "plan", str(brief), "--name", name, "--detach")
            assert drafted.returncode == 0, drafted.stderr
            wait_record(scratch / "draft-written")
            wait_ended(environment, name)
            history_path = (
                Path(environment["ONEPIPELINE_RUNS_DIR"]) / name / spike_brief.DELIVERY_HISTORY
            )
            match fault:
                case "initialize":
                    history_path.mkdir()
                case "launch":
                    malformed = fixture_action(
                        environment, "prepare-writer", project, name, scratch
                    )
                    assert malformed.returncode == 0, malformed.stderr
                case "writer":
                    # llmlint: ignore[e2e_not_mocked] Fail only the writer's billed paid turn.
                    environment["FAKE_BRIEF_FAILURE_MARKER"] = (
                        "Read local authoring project authoring:"
                    )
                case "spike":
                    # llmlint: ignore[e2e_not_mocked] Fail only one spike's billed paid turn.
                    environment["FAKE_BRIEF_FAILURE_MARKER"] = (
                        f"Measure and compare the spike-{project}-agent-5"
                    )
            launched = just(
                environment, "plan", str(brief), "--name", name, "--resume", "spikes", "--detach"
            )
            assert launched.returncode == 0, launched.stderr
            receipt_path = Path(environment["ONEPIPELINE_RUNS_DIR"]) / name / spike_brief.RECEIPT
            if fault in ["initialize", "launch"]:
                match fault:
                    case "initialize":
                        assert "spike brief advisory:" in launched.stderr
                        assert "spike_brief_flow launch" in launched.stderr
                        assert not receipt_path.exists()
                        history_path.rmdir()
                    case "launch":
                        assert "brief writer launch refused" in launched.stderr
                        saved_eligible = json.loads(history_path.read_text())["eligible"]
                        repaired = fixture_action(
                            environment, "repair-writer", project, name, scratch
                        )
                        assert repaired.returncode == 0, repaired.stderr
                retry = subprocess.run(
                    [
                        str(Path(environment["BRIEF_CHECKOUT"]) / ".venv/bin/python"),
                        "-m",
                        "orchestrator.spike_brief_flow",
                        "launch",
                        "authoring:" + project,
                        name,
                        name + "-spikes",
                    ],
                    cwd=environment["BRIEF_CHECKOUT"],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=e2e_timeout(60),
                    check=False,
                )
                assert retry.returncode == 0 and "advisory:" not in retry.stderr, retry.stderr
                if fault == "launch":
                    assert json.loads(history_path.read_text())["eligible"] == saved_eligible
            wait_record(scratch / "writer-reached")
            if fault in ["channel", "origin"]:
                # The success hook records the board copy and then fails to surface it, so
                # the brief is left undelivered with its board project named.
                channel = receipt_path.parent / "channel"
                for path in [*channel.iterdir(), channel]:
                    channel_modes[path] = path.stat().st_mode & 0o777
                    path.chmod(channel_modes[path] & ~0o222)
            (scratch / "writer-release").touch()
            wait_ended(environment, name + "-spike-brief")
            if fault in ["writer", "channel", "origin", "url"]:
                if fault in ["channel", "origin"]:
                    hook = (
                        receipt_path.parent.parent / (name + "-spike-brief") / "hooks/success.log"
                    )
                    wait_content(hook, "advisory")
                    assert "Permission denied" in hook.read_text(), hook.read_text()
                    for path, mode in channel_modes.items():
                        path.chmod(mode)
                    channel_modes.clear()
                if fault == "origin":
                    named = json.loads(receipt_path.read_text())["board_project"]
                    assert named is not None
                    # Another plan's copy, from a source that records its origin, takes over
                    # the recorded board project by title: it now belongs to that plan.
                    taken = rebind(environment, tmp_path, "authoring:" + project, decoy=True)
                    assert taken == named, (taken, named)
                    mismatched = brief_resume(environment, name)
                    assert mismatched.returncode == 0
                    assert "advisory:" in mismatched.stderr, mismatched.stderr
                    assert "another source project" in mismatched.stderr, mismatched.stderr
                    assert json.loads(receipt_path.read_text())["board_project"] == named
                    assert json.loads(receipt_path.read_text())["brief"] is None
                    # The plan's own copy, matched by title, takes the board project back.
                    assert rebind(environment, tmp_path, "authoring:" + project) == named
                if fault == "url":
                    hook = (
                        receipt_path.parent.parent / (name + "-spike-brief") / "hooks/success.log"
                    )
                    wait_content(hook, "advisory")
                    assert "HTTPS" in hook.read_text(), hook.read_text()
                    assert json.loads(receipt_path.read_text())["board_project"] is not None
                    assert json.loads(receipt_path.read_text())["brief"] is None
                    broken_board.clear()
                result = brief_resume(environment, name)
                assert result.returncode == 0
                if fault == "writer":
                    assert "advisory:" in result.stderr and "did not settle" in result.stderr
                    assert json.loads(receipt_path.read_text())["brief"] is None
                else:
                    assert "advisory:" not in result.stderr, result.stderr
            else:
                deadline = e2e_deadline(180)
                while json.loads(receipt_path.read_text())["brief"] is None:
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
            if fault != "writer":
                receipt = json.loads(receipt_path.read_text())
                assert receipt["brief"] is not None
                writes = [request for request in CloseoutBoard.requests if request.is_mutation]
                assert writes, "the initial whole-project copy made no board writes"
                for _ in range(2):
                    assert brief_resume(environment, name).returncode == 0
                assert [
                    request for request in CloseoutBoard.requests if request.is_mutation
                ] == writes
                queue = engine_command(environment, "channel", "queue", name)
                assert (
                    len(
                        [
                            row
                            for row in json.loads(queue.stdout)["surfaces"]
                            if row["kind"] == "check-in"
                        ]
                    )
                    == 1
                )
                assert "spike_briefs" in json.loads(
                    just(environment, "unfinished", "--session", name, "--json").stdout
                )
            (scratch / "spikes-release").touch()
            wait_ended(environment, name + "-spikes")
            status = engine_command(environment, "status", name + "-spikes", "--json")
            assert json.loads(status.stdout)["ending"]["kind"] == (
                "failed" if fault == "spike" else "complete"
            )
            assert "spike_briefs" not in json.loads(
                just(environment, "unfinished", "--session", name, "--json").stdout
            )
            if fault != "writer":
                refused = just(environment, "approve-spikes", "authoring:" + project)
                assert refused.returncode == 2 and "spikes have ended" in refused.stderr
            assert (
                len(
                    list(Path(environment["ONEPIPELINE_RUNS_DIR"]).glob("*spike-brief/launch.json"))
                )
                == 1
            )
        finally:
            for path, mode in channel_modes.items():
                path.chmod(mode)
            for artifact in ["writer-release", "spikes-release", "design-release"]:
                (scratch / artifact).touch()
            for run in [name, name + "-spikes", name + "-spike-brief"]:
                if (Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "launch.json").exists():
                    engine_command(environment, "stop", run)
