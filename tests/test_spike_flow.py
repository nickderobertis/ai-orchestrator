"""What `scripts/plan.sh` reads and writes between its stages, through `orchestrator/spike_flow.py`.

`tests/plan_tooling/test_plan_spike_flow_e2e.py` drives the whole flow through `just plan`.
This holds each answer the flow branches on against real stores: a spikes project written
into this process's own plan store and stamped through the store's own SDK, a run's ledger
record under a runs root of its own, and the branches a run kept as the pinned `onevcs`
records them in a scratch registry — including the shapes no launch produces on demand.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] These are the coverage tier's
measure of an `orchestrator/` module, whose every line the 100% floor `pyproject.toml` sets
must be covered by `orchestrator:test`; the real `git` and pinned `onevcs` they run are local,
seconds-long, and the boundary each answer is read from, so a project of their own would take
these lines out of the measurement the floor is held to.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
from pathlib import Path

import plan_fixture_source
import pytest
from conftest import git

from orchestrator import design_approval, plan_store, spike_branches, spike_flow
from orchestrator.plan_store import NodeId
from orchestrator.project_store import write_plan_project
from orchestrator.root import REPO_ROOT
from orchestrator.spike_plan import Branch

_SEQUENCE = itertools.count()
ONEVCS = REPO_ROOT / ".venv" / "bin" / "onevcs"
COMMITTER = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")


def _spikes_project(*nodes: str) -> str:
    native = f"spike-flow-{os.getpid()}-{next(_SEQUENCE)}-spikes"
    write_plan_project(
        plan_fixture_source.root(),
        {
            "schema_version": 3,
            "goal": {"text": "Measure before the plan is final"},
            "name": native,
            "tasks": [{"id": node, "task": "Measure."} for node in nodes],
        },
        native_id=native,
    )
    return f"{plan_fixture_source.SOURCE}:{native}"


def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: str, nodes: object) -> None:
    monkeypatch.setenv(spike_flow.RUNS_ROOT_ENV, str(tmp_path / "runs"))
    root = tmp_path / "runs" / run
    root.mkdir(parents=True)
    (root / spike_flow.RESULT).write_text(json.dumps({"nodes": nodes}), encoding="utf-8")


def test_a_draft_with_spikes_has_its_project_named_for_its_run_and_stamped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _spikes_project("spike-listing", "spike-quota")

    assert spike_flow.main(["prepare", project, "flow-spikes"]) == 0

    assert capsys.readouterr().out.split() == ["spike-listing", "spike-quota"]
    metadata = plan_store.project_record(project)["metadata"]
    assert isinstance(metadata, dict)
    assert metadata[spike_flow.NAME_KEY] == "flow-spikes"
    assert metadata[design_approval.PLAN_KIND] == {
        design_approval.STAMP_KIND: design_approval.SPIKES,
        design_approval.STAMP_NODES: ["spike-listing", "spike-quota"],
    }
    assert design_approval.stamped_launch(project) == design_approval.StampedLaunch(
        design_approval.SPIKES,
        frozenset({plan_store.NodeId("spike-listing"), plan_store.NodeId("spike-quota")}),
    )


def test_a_draft_that_wrote_no_spikes_project_is_answered_with_nothing() -> None:
    missing = f"{plan_fixture_source.SOURCE}:never-written-{os.getpid()}-spikes"

    assert spike_flow.prepare(missing, "flow-spikes") == []


def test_a_settled_spikes_run_answers_its_spikes_once_each_kept_its_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ledger(
        tmp_path,
        monkeypatch,
        "flow-spikes",
        [
            {"id": "spike-a", "status": "cancelled", "superseded_by": "spike-a-2"},
            {"id": "spike-a-2", "status": "done", "outcome": "preserved"},
            {"id": "spike-b", "status": "done", "outcome": "preserved"},
        ],
    )

    assert spike_flow.main(["settled", "flow-spikes", "--spikes"]) == 0
    assert capsys.readouterr().out.split() == ["spike-a-2", "spike-b"]


@pytest.mark.parametrize(
    ("nodes", "spikes", "said"),
    [
        ([{"id": "spike-a", "status": "failed"}], False, "spike-a failed"),
        (
            [{"id": "spike-a", "status": "done", "outcome": "merged"}],
            True,
            "spike-a done with the outcome merged, not preserved",
        ),
        ([], False, "records no node standing"),
        (
            [
                {"id": "spike-a", "status": "cancelled", "superseded_by": "spike-b"},
                {"id": "spike-b", "status": "cancelled", "superseded_by": "spike-a"},
                {"id": "spike-c", "status": "done"},
            ],
            False,
            "records a retry of spike-a replacing itself",
        ),
        (
            [{"id": "spike-a", "status": "done"}, {"id": "spike-a", "status": "failed"}],
            False,
            "records spike-a twice",
        ),
        (
            [
                {"id": "spike-a", "status": "cancelled", "superseded_by": "spike-c"},
                {"id": "spike-b", "status": "cancelled", "superseded_by": "spike-c"},
                {"id": "spike-c", "status": "done"},
            ],
            False,
            "records spike-c replacing both spike-a and spike-b",
        ),
        (
            [{"id": "spike-a", "status": "cancelled", "superseded_by": ["spike-b"]}],
            False,
            "which it holds no node for",
        ),
        ({"not": "a list"}, False, "records no list of nodes"),
        (["not a node"], False, "records a node with no id"),
        ([{"status": "done"}], False, "records a node with no id"),
    ],
    ids=[
        "failed",
        "not-preserved",
        "empty",
        "a-cycle",
        "a-node-twice",
        "two-replaced-by-one",
        "a-replacement-not-a-name",
        "not-a-list",
        "not-a-node",
        "no-id",
    ],
)
def test_a_run_not_settled_as_the_stage_needs_stops_naming_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    nodes: object,
    spikes: bool,
    said: str,
) -> None:
    _ledger(tmp_path, monkeypatch, "flow-spikes", nodes)

    assert spike_flow.main(["settled", "flow-spikes", *(["--spikes"] if spikes else [])]) == 1
    assert said in capsys.readouterr().err


def test_a_run_with_no_ledger_record_has_not_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(spike_flow.RUNS_ROOT_ENV, str(tmp_path / "runs"))

    with pytest.raises(spike_flow.Stop, match="run flow has not settled"):
        spike_flow.settled("flow")


def _kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: str, *nodes: str) -> None:
    """Leave each of ``nodes``' branches the way a spike node does, in a scratch registry.

    A node named ``""`` is a branch of the run whose session names no node.
    """
    monkeypatch.setenv("ONEVCS_HOME", str(tmp_path / "onevcs"))
    bare, checkout = tmp_path / "origin.git", tmp_path / "checkout"
    git("init", "-q", "--bare", "-b", "main", str(bare))
    git("init", "-q", "-b", "main", str(checkout))
    (checkout / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=checkout)
    git(*COMMITTER, "commit", "-qm", "chore: seed", cwd=checkout)
    git("remote", "add", "origin", str(bare), cwd=checkout)
    git("push", "-q", "origin", "main", cwd=checkout)

    def onevcs(*arguments: str) -> str:
        done = subprocess.run(
            [str(ONEVCS), *arguments], capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    onevcs("register", str(checkout))
    for node in nodes:
        opened = json.loads(
            onevcs(
                "session",
                "open",
                str(checkout),
                "--branch-name",
                f"plan/{node or 'unlabelled'}",
                "--label",
                f"run={run}",
                *(["--label", f"node={node}"] if node else []),
            )
        )
        worktree = Path(opened["worktree"])
        (worktree / "harness.sh").write_text(f"echo {node}\n", encoding="utf-8")
        git("add", "-A", cwd=worktree)
        git(*COMMITTER, "commit", "-qm", f"feat: {node}", cwd=worktree)
        onevcs("preserve", "--repo", str(checkout), opened["branch"])
        onevcs("session", "close", opened["token"])


def _preserved(*nodes: str) -> list[dict[str, str]]:
    return [{"id": node, "status": "done", "outcome": "preserved"} for node in nodes]


def test_each_spike_is_answered_with_the_branch_onevcs_recorded_for_its_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _kept(tmp_path, monkeypatch, "flow-spikes", "spike-a", "", "spike-b")
    _ledger(tmp_path, monkeypatch, "flow-spikes", _preserved("spike-a", "spike-b"))

    assert spike_flow.main(["branches", "flow-spikes"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "spike-a\tplan/spike-a",
        "spike-b\tplan/spike-b",
    ]


def test_a_spike_onevcs_recorded_no_branch_for_stops_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _kept(tmp_path, monkeypatch, "flow-spikes", "spike-a")
    _ledger(tmp_path, monkeypatch, "flow-spikes", _preserved("spike-a", "spike-c"))

    assert spike_flow.main(["branches", "flow-spikes"]) == 1
    assert "records no kept branch of run flow-spikes for spike-c" in capsys.readouterr().err


def test_a_retried_spike_is_answered_as_the_spike_with_the_branch_its_retry_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`spike-a` retried as `spike-a-2`: the branch is the retry's, the report the spike's."""
    _kept(tmp_path, monkeypatch, "flow-spikes", "spike-a-2")
    _ledger(
        tmp_path,
        monkeypatch,
        "flow-spikes",
        [
            {"id": "spike-a", "status": "cancelled", "superseded_by": "spike-a-2"},
            *_preserved("spike-a-2"),
        ],
    )

    assert spike_flow.main(["note", "authoring:a-new-plan", "flow-spikes"]) == 0
    assert "- `spike-a`: report `spike-a-report`; branch `plan/spike-a-2`" in (
        capsys.readouterr().out
    )


def test_a_ledger_naming_a_replacement_it_does_not_hold_has_not_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ledger(
        tmp_path,
        monkeypatch,
        "flow-spikes",
        [{"id": "spike-a", "status": "cancelled", "superseded_by": "spike-a-2"}],
    )

    with pytest.raises(spike_flow.Stop, match="superseded by 'spike-a-2', which it holds no node"):
        spike_flow.settled("flow-spikes")


def test_the_finalize_note_lists_every_report_and_branch_and_what_the_budgets_owe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _kept(tmp_path, monkeypatch, "flow-spikes", "spike-a")
    _ledger(tmp_path, monkeypatch, "flow-spikes", _preserved("spike-a"))

    assert spike_flow.main(["note", "authoring:a-new-plan", "flow-spikes"]) == 0
    budgeted = capsys.readouterr().out
    migrated = spike_flow.note(
        "authoring:approved-budgets", {NodeId("spike-a"): Branch("plan/spike-a")}
    )

    assert "- `spike-a`: report `spike-a-report`; branch `plan/spike-a`" in budgeted
    assert "`authoring:a-new-plan-budgets` current with what the spikes found" in budgeted
    assert "never in a budgets file" in budgeted
    assert "predates budgets" in migrated and "writes none" in migrated
    assert "escalated exception" in budgeted


def test_a_question_that_cannot_be_asked_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ledger(tmp_path, monkeypatch, "flow-spikes", _preserved("spike-a"))
    monkeypatch.setenv("ONEVCS_HOME", str(tmp_path / "unreadable"))
    (tmp_path / "unreadable").write_text("not a directory", encoding="utf-8")

    assert spike_flow.main(["branches", "flow-spikes"]) == 2
    assert capsys.readouterr().err.startswith("plan: ")


@pytest.mark.parametrize(
    "arguments",
    [["settled", "../elsewhere"], ["prepare", "authoring:p-spikes", "/tmp/x"]],
    ids=["settled", "prepare"],
)
def test_a_run_id_that_is_not_a_name_is_refused_before_anything_is_read(
    capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    """A run id is a directory under the runs root, so one that is not a name is a path."""
    project = _spikes_project("spike-a") if arguments[0] == "prepare" else ""
    called = [arguments[0], project, arguments[2]] if project else arguments

    assert spike_flow.main(called) == 2
    assert "is not a run id" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("answered", "said"),
    [
        ("{}", "answered no list of branches"),
        ('[{"labels": 7}]', "an entry of no known shape"),
        ('[{"labels": {"node": ""}, "branch": {"branch": "b"}}]', "an entry of no known shape"),
        ('[{"labels": {"node": "spike-a"}, "branch": {"branch": ""}}]', "of no known shape"),
        (
            '[{"labels": {"node": "spike-a\\u0009x"}, "branch": {"branch": "b"}}]',
            "of no known shape",
        ),
        (
            '[{"labels": {"node": "spike-a"}, "branch": {"branch": "b"}},'
            ' {"labels": {"node": "spike-a"}, "branch": {"branch": "c"}}]',
            "answered two branches for spike-a: b and c",
        ),
    ],
    ids=[
        "not-a-list",
        "an-entry-of-no-shape",
        "a-blank-node",
        "a-blank-branch",
        "a-node-splitting-the-record",
        "two-branches-for-one-node",
    ],
)
def test_a_recoverable_answer_of_no_known_shape_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answered: str,
    said: str,
) -> None:
    answering = tmp_path / "onevcs"
    answering.write_text(f"#!/bin/sh\necho '{answered}'\n", encoding="utf-8")
    answering.chmod(0o755)
    monkeypatch.setattr(spike_branches, "installed_onevcs", lambda: str(answering))
    _ledger(tmp_path, monkeypatch, "flow-spikes", _preserved("spike-a"))

    assert spike_flow.main(["branches", "flow-spikes"]) == 2
    assert said in capsys.readouterr().err


def test_the_run_grammar_and_runs_root_are_the_ones_plan_brief_sh_names() -> None:
    """`scripts/plan-brief.sh` names the flow's runs and where they are found; this reads them."""
    helper = (REPO_ROOT / "scripts" / "plan-brief.sh").read_text(encoding="utf-8")

    assert f'PLAN_RUNS_ROOT_ENV="{spike_flow.RUNS_ROOT_ENV}"' in helper
    assert f'PLAN_DEFAULT_RUNS_ROOT="{spike_flow.DEFAULT_RUNS_ROOT}"' in helper
    declared = next(
        line.split("=", 1)[1].strip("'")
        for line in helper.splitlines()
        if line.startswith("PLAN_SAFE_RUN_ID=")
    )
    # The shell's anchored `^…$` and Python's `fullmatch` against `…\Z` say one thing.
    assert spike_flow.RUN_ID.pattern == declared.removeprefix("^").removesuffix("$") + r"\Z"


def test_a_spikes_project_that_is_not_a_qualified_id_is_refused_rather_than_read_as_absent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert spike_flow.main(["prepare", "authoring", "flow-spikes"]) == 2
    assert "'authoring' is not a qualified project id" in capsys.readouterr().err


def test_a_stage_run_launched_from_another_project_is_not_this_flows_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A resume reads a stage by its run's name; a run of that name from elsewhere is refused."""
    _ledger(tmp_path, monkeypatch, "flow-spikes", _preserved("spike-a"))
    launch = tmp_path / "runs" / "flow-spikes" / spike_flow.LAUNCH_RECORD
    launch.write_text(json.dumps({"project": "authoring:other-plan-spikes"}), encoding="utf-8")

    own = ["settled", "flow-spikes", "--project", "authoring:other-plan-spikes"]
    assert spike_flow.main(own) == 0
    capsys.readouterr()
    assert spike_flow.main(["settled", "flow-spikes", "--project", "authoring:p-spikes"]) == 1
    assert "launched from 'authoring:other-plan-spikes', not from authoring:p-spikes" in (
        capsys.readouterr().err
    )
    launch.unlink()
    assert spike_flow.main(["settled", "flow-spikes", "--project", "authoring:p-spikes"]) == 1
    assert "has not settled" in capsys.readouterr().err


def test_a_note_for_a_plan_that_is_not_a_qualified_id_is_refused() -> None:
    with pytest.raises(ValueError, match="'a-new-plan' is not a qualified project id"):
        spike_flow.note("a-new-plan", {})


@pytest.mark.parametrize(
    ("project", "said"),
    [
        (f"{plan_fixture_source.SOURCE}:a-plan", "is not a plan's spikes project"),
        (f"{plan_fixture_source.SOURCE}:p{{{{node.id}}}}-spikes", "holds a character a branch"),
    ],
    ids=["not-a-spikes-project", "a-plan-id-a-template-reads"],
)
def test_a_spikes_project_whose_plan_id_cannot_name_its_branches_is_refused(
    capsys: pytest.CaptureFixture[str], project: str, said: str
) -> None:
    """Refused before anything is launched: the id goes into the spikes' branch template."""
    assert spike_flow.main(["prepare", project, "flow-spikes"]) == 2
    assert said in capsys.readouterr().err
