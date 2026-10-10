"""The plan checklist judges exactly one plan's documents, as a planner's second judge.

`scripts/plan-checklist.sh` runs the real llmlint under `config/plan-checklist.llmlint.yml`
over the documents of one plan — its description and every task document the plan store
locates — from the plan-authoring root. It runs in two places, and each is driven here the
way it is reached: the review form, as `just review-plan` runs it, and the llmlint side of
the planner's panel (`graphs/planner.yaml`), which the pinned `oneagentgraph` composes and
the pinned `onejudge` runs with the script itself as the side's `bin`.

The plan is the committed copy of the real `authoring:nonfunctional-requirements-are-budgets`
plan, `tests/fixtures/plan-checklist-plans/`, beside a second plan in the same authoring root
that the checklist must never read. Its task bodies quote this host's dispatch appendix,
`llmlint: ignore` directive included, which llmlint's structural pre-flight would refuse
before any judge ran.

Only the paid model is doubled, at the harness boundary: llmlint's judge reaches the real
`oneharness` through `scripts/llmlint-oneharness.sh`, which spawns the codex stand-in, and
the guard directory refuses every other provider binary. The panel's harness sides speak
onejudge's provider protocol through `judge_protocol_double.py`, as the panel journey does.
What each process was handed is read back off the processes themselves: the script's argv
through bash's own `BASH_ENV`, which bash sources before running it, and the real llmlint's
through a recording `llmlint` placed first on `PATH`, which execs the installed one.

These read the llmlint, onejudge and oneagentgraph this host installed, outside the
workspace and so outside every `nx.json` key, so they run in the uncached tier.
"""

# The findings these answer are about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it. The journeys run the
# llmlint, onejudge and oneagentgraph this host installed, which live outside the workspace
# and so outside every `nx.json` key: the uncached `orchestrator:test-checkouts` tier
# exists for exactly that, selected by `reads_checkouts`, as `test_judge_panel_e2e.py`'s
# are.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above
# The shell these drive is the one checklist script, through the installed tools the marker
# routes to that uncached tier, so a project of its own would be keyed on the same nothing.
# llmlint: ignore-block[shell_test_tiers_stay_split] see above

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import plan_root_variable
import pytest
import yaml
from fake_backend import PANEL_JUDGE_CONFIG
from harness_indirections import established_indirections
from onejudge_sdk import OneJudge, ProviderConfig, RunConfig, RunResult, UserConfig
from persona_probe import probe_environment
from project_fixtures import helper

from orchestrator import plan_checklist_budgets as budgets
from orchestrator.root import REPO_ROOT

pytestmark = [
    pytest.mark.reads_checkouts,
    pytest.mark.skipif(shutil.which("llmlint") is None, reason="run 'just setup-llmlint'"),
]

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "plan-checklist-plans"
SCRIPT = REPO_ROOT / "scripts" / "plan-checklist.sh"
CONFIG = REPO_ROOT / "config" / "plan-checklist.llmlint.yml"
GRAPH = REPO_ROOT / "graphs" / "planner.yaml"
PLAN = "nonfunctional-requirements-are-budgets"
PROJECT = f"authoring:{PLAN}"
#: The second plan of the authoring root, which no run here may read.
OTHER = "another-plan"
#: An ignore directive's opening, assembled rather than spelled whole, so this module's own
#: source carries no directive llmlint's pre-flight would read as one of its own.
DIRECTIVE = "llmlint" + ": ignore"
#: What the `plan` agent's appended template opens with, so a prompt carrying it was framed
#: as a plan rather than as code.
FRAMING = "## You are reading a plan, not code"
#: A task of the plan, and the rule its stand-in judge is told to fail there.
TASK = "tasks/feat-check-print-each-result-s-detail-under-its-line-in-text-output.md"
DESCRIPTION = f"projects/{PLAN}.md"
TASK_RULE = "tests_hold_no_nonfunctional_thresholds"
#: The task whose budget leaves how `own-sessions-read-seconds` is taken to its worker.
BUDGETS_TASK = "tasks/feat-budgets-move-the-cost-thresholds-tests-assert-into-budgets-yaml-files.md"
PLAN_RULE = "budget_commands_measure_directly"
#: The documents the plan store locates for the plan, relative to the authoring root.
DOCUMENTS = {DESCRIPTION, *(f"tasks/{task.name}" for task in (FIXTURE / "tasks").iterdir())}

# llmlint: ignore[e2e_not_mocked] Only the paid model is substituted; see the docstring.
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
DOUBLE = helper("judge_protocol_double.py")
PINNED_ONEJUDGE = Path(sys.executable).with_name("onejudge")
PINNED_ONEAGENTGRAPH = Path(sys.executable).with_name("oneagentgraph")
RUN_TIMEOUT_SECONDS = 240

#: Sourced by bash before it runs any script; records the plan-checklist script's argv.
RECORD_SCRIPT = """\
case "$0" in */plan-checklist.sh)
  "$RECORDING_PYTHON" -c '
import json, sys
open(sys.argv[1], "a").write(json.dumps(sys.argv[2:]) + "\\n")
' "$SCRIPT_ARGV_LOG" "$@" ;;
esac
"""

#: The `llmlint` first on `PATH`: records its argv and what its `--cwd` holds, then execs
#: the installed llmlint unchanged.
RECORD_LLMLINT = """\
#!{python}
import json, os, sys
argv = sys.argv[1:]
cwd = argv[argv.index("--cwd") + 1] if "--cwd" in argv else None
holds = sorted(os.listdir(cwd)) if cwd else None
with open(os.environ["LLMLINT_ARGV_LOG"], "a") as log:
    log.write(json.dumps({{"argv": argv, "cwd_holds": holds}}) + "\\n")
os.execv({real!r}, [{real!r}, *argv])
"""


@dataclass
class Checklist:
    """One journey's authoring root, recordings, and the environment that reaches them."""

    root: Path
    environment: dict[str, str]
    script_log: Path
    llmlint_log: Path
    prompt_log: Path

    def script_argv(self) -> list[list[str]]:
        return [json.loads(line) for line in _lines(self.script_log)]

    def llmlint_runs(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in _lines(self.llmlint_log)]

    def prompts(self) -> list[str]:
        return [json.loads(line)["prompt"] for line in _lines(self.prompt_log)]

    def depart(self, document: str, rule: str, reason: str, *, decided: bool = False) -> None:
        """Write the departure line the planner writes, where the planner writes it."""
        path = self.root / document
        # The body only: a description's frontmatter restates its overview, headings
        # included, and a line written there would be YAML rather than the document.
        _, front, body = path.read_text(encoding="utf-8").split("---\n", 2)
        heading = "## What this plan delivers" if decided else "## Additional info"
        assert heading in body, f"{document}'s body has no {heading!r} to write under"
        line = f"<!-- {DIRECTIVE}-file[{rule}] {reason} -->"
        lead = "**Decided** — an exception the user approved.\n\n" if decided else ""
        body = body.replace(heading, f"{heading}\n\n{lead}{line}\n", 1)
        path.write_text(f"---\n{front}---\n{body}", "utf-8")


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.fixture
def checklist(tmp_path: Path) -> Iterator[Checklist]:
    """An authoring root holding the fixture plan and another, and the recorders."""
    root = tmp_path / "authoring"
    shutil.copytree(FIXTURE, root)
    # The other plan: the fixture's description and one task, moved to a project of its own.
    other = (root / DESCRIPTION).read_text(encoding="utf-8")
    (root / "projects" / f"{OTHER}.md").write_text(other, encoding="utf-8")
    task = (
        (root / TASK).read_text(encoding="utf-8").replace(f"project: {PLAN}", f"project: {OTHER}")
    )
    (root / "tasks" / "another-task.md").write_text(task, encoding="utf-8")

    recorders = tmp_path / "recorders"
    recorders.mkdir()
    real = shutil.which("llmlint")
    assert real is not None
    shim = recorders / "llmlint"
    shim.write_text(RECORD_LLMLINT.format(python=sys.executable, real=real), encoding="utf-8")
    shim.chmod(0o755)
    bash_env = tmp_path / "record-script.sh"
    bash_env.write_text(RECORD_SCRIPT, encoding="utf-8")

    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ONEHARNESS_", "FAKE_CODEX_", "ONEPIPELINE_", "LLMLINT_"))
    }
    recorded = Checklist(
        root,
        environment,
        tmp_path / "script-argv.jsonl",
        tmp_path / "llmlint-argv.jsonl",
        tmp_path / "prompts.jsonl",
    )
    environment |= {
        plan_root_variable.plugin_name(): "local-md",
        plan_root_variable.name(): str(root),
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
        "FAKE_CODEX_LLMLINT_PROMPT_LOG": str(recorded.prompt_log),
        "PATH": os.pathsep.join([str(PAID_PROVIDER_GUARD), str(recorders), environment["PATH"]]),
        "BASH_ENV": str(bash_env),
        "RECORDING_PYTHON": sys.executable,
        "SCRIPT_ARGV_LOG": str(recorded.script_log),
        "LLMLINT_ARGV_LOG": str(recorded.llmlint_log),
        "LLMLINT_HISTORY_DIR": str(tmp_path / "llmlint-history"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    yield recorded


def _scope(prompt: str, rule: str) -> str:
    """The `Scope:` a judge prompt gives ``rule``."""
    section = prompt.split(f"### {rule}\n", 1)[1].split("\n### ", 1)[0]
    (scope,) = [line for line in section.splitlines() if line.startswith("Scope: ")]
    return scope.removeprefix("Scope: ")


def _targets(prompt: str) -> set[str]:
    """The files a judge prompt lists under its `## Target files`."""
    listed = prompt.split("## Target files", 1)[1].split("## Rules to evaluate", 1)[0]
    return {line[2:].strip() for line in listed.splitlines() if line.startswith("- ")}


def test_the_review_form_judges_exactly_the_plans_documents_as_a_plan(
    checklist: Checklist,
) -> None:
    """Every task quotes an ignore directive, and the run still reaches the judge.

    The store answers two items a page, so the plan's six tasks come back over three pages,
    and a listing that stopped at the first would leave four documents unjudged.
    """
    assert all(
        f"{DIRECTIVE}[" in (checklist.root / document).read_text(encoding="utf-8")
        for document in DOCUMENTS - {DESCRIPTION}
    ), "the fixture's task bodies no longer quote the dispatch appendix's directive"

    ran = subprocess.run(
        [str(SCRIPT), "review", PROJECT, "--format", "json"],
        cwd=REPO_ROOT,
        env=checklist.environment | {"ONETASKGRAPH_PAGE_SIZE": "2"},
        text=True,
        capture_output=True,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )

    assert ran.returncode == 0, ran.stdout + ran.stderr
    (prompt,) = checklist.prompts()
    assert FRAMING in prompt
    assert _targets(prompt) == DOCUMENTS
    assert not any(OTHER in target or "another-task" in target for target in _targets(prompt))
    (run,) = checklist.llmlint_runs()
    argv = run["argv"]
    assert isinstance(argv, list)
    assert argv[:6] == [
        "lint",
        "--cwd",
        str(checklist.root),
        "-c",
        str(CONFIG),
        "--no-ignore-check",
    ]
    assert sorted(argv[-len(DOCUMENTS) :]) == sorted(DOCUMENTS)
    # Judged rather than stopped at the pre-flight: every rule the plan agent holds answered.
    assert json.loads(ran.stdout)["summary"]["passed"] == len(_judged_rules())


def _declared_rules() -> list[dict[str, object]]:
    rules: list[dict[str, object]] = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["rules"]
    return rules


def _judged_rules() -> list[dict[str, object]]:
    """Every rule the configuration has judged: all but the fragment rules turned off."""
    return [rule for rule in _declared_rules() if rule.get("relevance") is not False]


def _worded_rules() -> list[dict[str, object]]:
    """The rules the configuration words itself, rather than re-declaring a fragment's."""
    return [rule for rule in _declared_rules() if not rule.get("override")]


def _in_scope(scope: str) -> set[str]:
    """The plan's documents a judge prompt's `Scope:` line covers."""
    if scope == "all target files":
        return set(DOCUMENTS)
    if scope.startswith("all target files except: "):
        return DOCUMENTS - {part.strip() for part in scope.split(": ", 1)[1].split(",")}
    return {part.strip() for part in scope.split(",")}


def test_each_rule_the_checklist_words_is_judged_over_the_plans_documents(
    checklist: Checklist,
) -> None:
    """Each of the checklist's own rules reaches the judge, scoped to the documents it reads.

    A task rule selects the task documents and a description rule the description, each by
    the path the store keeps it at, and its verdict reaches the report under its own name:
    the two the stand-in judge is told to fail, on the either/or a budget leaves its worker
    and on the description's 10x answer, fail there, and every other one passes.
    """
    worded = _worded_rules()
    assert len(worded) == 11, [rule["name"] for rule in worded]
    failing = {
        "plan_budget_measurement_is_decided": {
            "file": BUDGETS_TASK,
            "line": 55,
            "message": "leaves how own-sessions-read-seconds is taken to the worker",
        },
        "plan_ten_x_names_a_covering_budget": {
            "file": DESCRIPTION,
            "line": 232,
            "message": "names no budget the effect grows",
        },
    }

    ran = subprocess.run(
        [str(SCRIPT), "review", PROJECT, "--format", "json"],
        cwd=REPO_ROOT,
        env=checklist.environment | {"FAKE_CODEX_LLMLINT_FAIL": json.dumps(failing)},
        text=True,
        capture_output=True,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )

    assert ran.returncode == 1, ran.stdout + ran.stderr
    (prompt,) = checklist.prompts()
    assert FRAMING in prompt
    outcomes = {rule["name"]: rule for rule in json.loads(ran.stdout)["rules"]}
    for rule in worded:
        name = str(rule["name"])
        description = str(rule["description"]).split("\n", 1)[0].strip()
        section = prompt.split(f"### {name}\n", 1)[1].split("\n### ", 1)[0]
        assert description in section, f"{name} reached the judge without its own wording"
        files = rule["files"]
        assert isinstance(files, dict)
        (glob,) = files["include"]
        selected = {document for document in DOCUMENTS if PurePosixPath(document).full_match(glob)}
        assert selected, f"{name}'s {glob} selects none of the plan's documents"
        assert _in_scope(_scope(prompt, name)) == selected, name
        expected = "fail" if name in failing else "pass"
        assert outcomes[name]["outcome"] == expected, outcomes[name]


def _graph_judges() -> list[dict[str, str]]:
    """The judge entries `graphs/planner.yaml` declares, as the shipped document states them."""
    declared = yaml.safe_load(GRAPH.read_text(encoding="utf-8"))
    judges: list[dict[str, str]] = declared["members"]["worker"]["judge"]
    return judges


def test_the_planner_graph_validates_and_composes_its_reviewer_then_the_plan_checklist(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The pinned oneagentgraph composes the panel; onejudge then refuses to start it.

    The composition is read off the onejudge configuration the pinned oneagentgraph hands
    its member. The refusal is the reason no planning node names this graph yet: onejudge
    refuses a panel holding this host's writable-mode reviewer, and the graph has no field
    that carries `allow_writable_judges` to the split. A release that adds one makes this
    journey fail here, which is the signal to set it and name the graph in `scripts/plan.sh`.
    """
    validated = subprocess.run(
        [str(PINNED_ONEAGENTGRAPH), "validate", str(GRAPH)],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    environment = probe_environment(tmp_path, oneharness_bin, "plan-checklist journey")
    environment["ONEAGENTGRAPH_STATE_DIR"] = str(tmp_path / "graph-state")

    ran = subprocess.run(
        [str(PINNED_ONEAGENTGRAPH), "run", str(GRAPH), "--task", "Plan the work."],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )

    envelopes = [json.loads(line) for line in ran.stdout.splitlines() if line.strip()]
    (started,) = [envelope for envelope in envelopes if envelope["kind"] == "member-started"]
    composed = yaml.safe_load(Path(started["payload"]["config"]).read_text(encoding="utf-8"))
    provider = composed["provider"]
    assert provider["kind"] == "split"
    judges = provider["judges"]
    assert [(judge["label"], judge["kind"]) for judge in judges] == [
        ("reviewer", "oneharness"),
        ("plan-checklist", "llmlint"),
    ]
    # The name oneagentgraph writes a panel's harness judge config under is the one the
    # agent-side stand-in reads a judge turn by, so the two cannot drift apart unnoticed.
    assert PANEL_JUDGE_CONFIG.fullmatch(Path(judges[0]["judge_config"]).name), judges[0]
    assert judges[1]["bin"] == "scripts/plan-checklist.sh"
    assert Path(judges[1]["config"]).resolve() == CONFIG
    (died,) = [envelope for envelope in envelopes if envelope["kind"] == "member-died"]
    assert "allow_writable_judges" in died["payload"]["detail"], died


# A real planning run would launch the engine and dispatch a paid planner; what the
# checklist reads of one is its ledger's `plan.json`, the one task of which is the brief, so
# that record is written as the engine writes it and nothing else of a run is.
# llmlint: ignore-block[tests_mirror_real_usage] see above
def _planning_run(tmp_path: Path, project: str | None) -> dict[str, str]:
    """A planning run whose one task is a brief naming ``project``, as the dispatch sees it."""
    runs = tmp_path / "runs"
    run = "plan-journey"
    (runs / run).mkdir(parents=True)
    declared = f"Plan project: {project}\n" if project else ""
    brief = f"# Plan it\n\n{declared}\n## What\n\nPlan it.\n"
    (runs / run / "plan.json").write_text(
        json.dumps({"tasks": [{"id": "plan", "task": brief}]}), encoding="utf-8"
    )
    return {"ONEPIPELINE_RUNS_DIR": str(runs), "ONEPIPELINE_RUN_ID": run}


# llmlint: ignore-end[tests_mirror_real_usage]


def _panel(worktree: Path, checklist: Checklist, tmp_path: Path, project: str | None) -> RunResult:
    """Run the pinned onejudge under the planner's panel, its llmlint side the script.

    Each judge is the shipped graph's own entry, in its order and under its label: the
    reviewer's harness side answered by the protocol double, the llmlint side exactly as
    the graph declares it, its `bin` named from this checkout's root as a run launched
    here resolves it and its `config` anchored to the graph's directory.
    """
    reviewer_entry, checklist_entry = _graph_judges()
    # The panel's harness sides are the paid model's, so they are the protocol double; the
    # judge under test is the llmlint side, which is the real script and the real llmlint.
    # llmlint: ignore[e2e_not_mocked] Only the paid model is substituted; see above.
    reviewer = ProviderConfig(
        kind="command", command=[sys.executable, str(DOUBLE), "reviewer", "0"]
    )
    reviewer["label"] = reviewer_entry["label"]
    plan_checklist = ProviderConfig(
        kind="llmlint",
        bin=str(REPO_ROOT / checklist_entry["bin"]),
        config=str((GRAPH.parent / checklist_entry["config"]).resolve()),
    )
    plan_checklist["label"] = checklist_entry["label"]
    config = RunConfig(
        provider={
            "kind": "split",
            # llmlint: ignore[e2e_not_mocked] The worker's harness side is the paid model's.
            "skill": ProviderConfig(
                kind="command", command=[sys.executable, str(DOUBLE), "worker", "0"]
            ),
            "judges": [reviewer, plan_checklist],
        },
        user=UserConfig(
            persona="A reviewer holding a plan to its brief.",
            done_when="the plan answers the brief",
            max_turns=2,
        ),
    )
    environment = checklist.environment | _planning_run(tmp_path, project)
    client = OneJudge(executable=str(PINNED_ONEJUDGE))
    return asyncio.run(
        client.run(
            config,
            "Plan the work the brief names.",
            cwd=str(worktree),
            env=environment,
            timeout=RUN_TIMEOUT_SECONDS,
        )
    )


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """The directory onejudge runs the panel in, holding a file no checklist may judge."""
    made = tmp_path / "worktree"
    made.mkdir()
    (made / "README.md").write_text("# The planner's checkout\n", encoding="utf-8")
    return made


def _turns(result: RunResult) -> list[list[tuple[str, str]]]:
    return [
        [(one["judge"], one["decision"]) for one in turn["decisions"]]
        for turn in result.raw.get("judge_decisions", ())
    ]


def test_a_failing_rule_reaches_the_worker_under_the_plan_checklists_header(
    checklist: Checklist, worktree: Path, tmp_path: Path
) -> None:
    """Each process got the argv it should, and the failing rule is the worker's to fix."""
    checklist.environment["FAKE_CODEX_LLMLINT_FAIL"] = json.dumps(
        {TASK_RULE: {"file": TASK, "line": 5, "message": "a test asserts a wall clock"}}
    )
    # oneharness history on, as a dispatch has it, through a pointer file of this run's own.
    pointers = tmp_path / "oneharness-sessions.jsonl"
    checklist.environment |= {
        "ONEHARNESS_HISTORY": "1",
        "ONEHARNESS_HISTORY_POINTER_FILE": str(pointers),
        "ONEHARNESS_HISTORY_DIR": str(tmp_path / "harness-history"),
    }

    result = _panel(worktree, checklist, tmp_path, PROJECT)

    assert _turns(result)[0] == [("reviewer", "done"), ("plan-checklist", "continue")]
    handed = result.raw["transcript"]["messages"][2]["content"]
    assert "## Judge `plan-checklist` (llmlint)" in handed, handed
    assert f"FAIL {TASK_RULE}" in handed and "a test asserts a wall clock" in handed, handed

    probe, *decisions = checklist.script_argv()
    assert probe == ["--version"]
    first, *_ = decisions
    # onejudge names the worktree it runs in, relative to the directory it runs from.
    assert first[:2] == ["lint", "--cwd"] and (worktree / first[2]).resolve() == worktree
    llmlint_probe, *lints = checklist.llmlint_runs()
    assert llmlint_probe["argv"] == ["--version"]
    for given, ran in zip(decisions, lints, strict=True):
        argv = ran["argv"]
        assert isinstance(argv, list)
        assert argv.count("lint") == 1 and argv[0] == "lint"
        assert argv.count("-c") == 1 and argv[argv.index("-c") + 1] == str(CONFIG)
        assert argv.count("--cwd") == 1 and argv[argv.index("--cwd") + 1] == str(checklist.root)
        # onejudge's own argv in order, its `--cwd` rewritten, then the two additions.
        at = given.index("--cwd") + 1
        rewritten = [*given[:at], str(checklist.root), *given[at + 1 :]]
        assert argv[: len(given)] == rewritten
        assert argv[len(given)] == "--no-ignore-check"
        assert sorted(argv[len(given) + 1 :]) == sorted(DOCUMENTS)

    # Every judge call each decision made is recorded under one label naming that run, so
    # the plan-checklist budgets tell one decision's calls from the next.
    recorded = [json.loads(line) for line in pointers.read_text(encoding="utf-8").splitlines()]
    named = [pointer["labels"].get(budgets.RUN_LABEL) for pointer in recorded]
    assert all(pointer["labels"]["role"] == "llmlint" for pointer in recorded), recorded
    assert None not in named and len(set(named)) == len(lints), named
    found, missing = budgets.calls(pointers, by_role=True)
    assert missing == [] and len(budgets.decisions(found)) == len(lints)
    # And the report the pinned onejudge writes is one the budgets read a reviewer's turns
    # from, in the shape they read it.
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "planner.json").write_text(json.dumps(result.raw), encoding="utf-8")
    # Both judges here are command providers, which name no harness identity, so onejudge
    # records no attribution at all and the reviewer has no harness turn to count.
    assert budgets.reviewer_seconds(reports) == []


def test_onejudges_cwd_is_rewritten_in_either_spelling(
    checklist: Checklist, worktree: Path, tmp_path: Path
) -> None:
    """`--cwd=<dir>` is rewritten in place as `--cwd <dir>` is, and never appended twice."""
    ran = subprocess.run(
        [str(SCRIPT), "lint", f"--cwd={worktree}", "-c", str(CONFIG), "--plan-only"],
        cwd=worktree,
        env=checklist.environment | _planning_run(tmp_path, PROJECT),
        text=True,
        capture_output=True,
        timeout=RUN_TIMEOUT_SECONDS,
        check=False,
    )

    assert ran.returncode == 0, ran.stdout + ran.stderr
    (run,) = checklist.llmlint_runs()
    argv = run["argv"]
    assert isinstance(argv, list)
    assert [word for word in argv if word.startswith("--cwd")] == [f"--cwd={checklist.root}"]
    assert argv[:4] == ["lint", f"--cwd={checklist.root}", "-c", str(CONFIG)]
    assert argv[5] == "--no-ignore-check"
    assert sorted(argv[6:]) == sorted(DOCUMENTS)


def test_a_task_departure_from_the_rule_is_honoured_and_the_panel_completes(
    checklist: Checklist, worktree: Path, tmp_path: Path
) -> None:
    checklist.environment["FAKE_CODEX_LLMLINT_FAIL"] = json.dumps(
        {TASK_RULE: {"file": TASK, "line": 5, "message": "a test asserts a wall clock"}}
    )
    checklist.depart(TASK, TASK_RULE, "the user approved a bounded wait in this one test")

    result = _panel(worktree, checklist, tmp_path, PROJECT)

    assert result.completed, result.stderr
    assert _turns(result)[0] == [("reviewer", "done"), ("plan-checklist", "done")]
    # Judged, with the departing task out of that one rule's scope.
    assert _scope(checklist.prompts()[0], TASK_RULE) == f"all target files except: {TASK}"


def test_a_plan_level_departure_on_the_description_is_honoured(
    checklist: Checklist, worktree: Path, tmp_path: Path
) -> None:
    checklist.environment["FAKE_CODEX_LLMLINT_FAIL"] = json.dumps(
        {PLAN_RULE: {"file": DESCRIPTION, "line": 3, "message": "a per-budget wrapper"}}
    )
    checklist.depart(DESCRIPTION, PLAN_RULE, "approved: one runner serves both", decided=True)

    result = _panel(worktree, checklist, tmp_path, PROJECT)

    assert result.completed, result.stderr
    assert _turns(result)[0] == [("reviewer", "done"), ("plan-checklist", "done")]
    assert _scope(checklist.prompts()[0], PLAN_RULE) == f"all target files except: {DESCRIPTION}"


def test_a_departure_naming_a_misspelled_rule_leaves_the_rule_failing(
    checklist: Checklist, worktree: Path, tmp_path: Path
) -> None:
    checklist.environment["FAKE_CODEX_LLMLINT_FAIL"] = json.dumps(
        {TASK_RULE: {"file": TASK, "line": 5, "message": "a test asserts a wall clock"}}
    )
    checklist.depart(TASK, "tests_hold_no_nonfunctional_threshold", "misspelled")

    result = _panel(worktree, checklist, tmp_path, PROJECT)

    assert _turns(result)[0] == [("reviewer", "done"), ("plan-checklist", "continue")]


@pytest.mark.parametrize(
    "project", ["authoring:not-written-yet", None], ids=["unwritten", "no-plan-project"]
)
def test_before_the_plan_has_a_document_the_checklist_judges_nothing_and_completes(
    checklist: Checklist, worktree: Path, tmp_path: Path, project: str | None
) -> None:
    """A plan the store does not hold yet, or a brief naming none: nothing to judge."""
    result = _panel(worktree, checklist, tmp_path, project)

    assert result.completed, result.stderr
    assert _turns(result)[0] == [("reviewer", "done"), ("plan-checklist", "done")]
    assert checklist.prompts() == [], "a judge was paid for a plan with no document"
    _, *lints = checklist.llmlint_runs()
    for ran in lints:
        argv = ran["argv"]
        assert isinstance(argv, list)
        directory = Path(argv[argv.index("--cwd") + 1])
        assert ran["cwd_holds"] == [], ran
        assert directory != worktree and REPO_ROOT not in directory.parents


def test_the_budgets_read_the_reviewers_turns_off_a_report_the_pinned_onejudge_writes(
    checklist: Checklist, worktree: Path, tmp_path: Path, oneharness_bin: str
) -> None:
    """A real harness reviewer beside the checklist: its attributed turns are what is read.

    The reviewer is the real `oneharness` over this host's `oneharness.judge.toml`, labelled
    as the planner's panel labels it, with only its provider stood in; onejudge's own
    `allow_writable_judges` accepts its writable mode here, which the graph cannot yet say.
    The report onejudge writes is then read by the plan-checklist budgets as a dispatch's
    report is, so a change to how onejudge attributes a panel judge's turns fails here.
    """
    reviewer_entry, checklist_entry = _graph_judges()
    plan_checklist = ProviderConfig(
        kind="llmlint",
        bin=str(REPO_ROOT / checklist_entry["bin"]),
        config=str((GRAPH.parent / checklist_entry["config"]).resolve()),
    )
    plan_checklist["label"] = checklist_entry["label"]
    reviewer = ProviderConfig(
        kind="oneharness",
        bin=oneharness_bin,
        judge_config=str(REPO_ROOT / "oneharness.judge.toml"),
    )
    reviewer["label"] = reviewer_entry["label"]
    config = RunConfig(
        provider={
            "kind": "split",
            # llmlint: ignore[e2e_not_mocked] The worker's harness side is the paid model's.
            "skill": ProviderConfig(
                kind="command", command=[sys.executable, str(DOUBLE), "worker", "0"]
            ),
            "judges": [reviewer, plan_checklist],
            "allow_writable_judges": True,
        },
        user=UserConfig(
            persona="A reviewer holding a plan to its brief.",
            done_when="the plan answers the brief",
            max_turns=2,
        ),
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    environment = (
        checklist.environment
        | dict(established_indirections("plan-checklist journey"))
        | _planning_run(tmp_path, PROJECT)
        | {
            "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
            "FAKE_CODEX_ATTEMPT_LOG": str(tmp_path / "launches"),
            "FAKE_CODEX_ANSWERS": json.dumps(
                [
                    json.dumps({"completion": True, "reason": "the stand-in accepts the plan"}),
                    json.dumps({"value": True, "reason": "the stand-in holds the bar met"}),
                ]
            ),
        }
    )

    result = asyncio.run(
        OneJudge(executable=str(PINNED_ONEJUDGE)).run(
            config,
            "Plan the work the brief names.",
            cwd=str(worktree),
            env=environment,
            timeout=RUN_TIMEOUT_SECONDS,
        )
    )

    assert result.completed, result.stderr
    attribution = result.raw["telemetry"]["attribution"]
    reviewers = [
        turn
        for turn in attribution
        if turn["role"] == "judge" and turn.get("judge") == reviewer_entry["label"]
    ]
    assert reviewers, attribution
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "planner.json").write_text(json.dumps(result.raw), encoding="utf-8")
    assert budgets.reviewer_seconds(reports) == [
        sum(candidate["duration_ms"] for candidate in turn["candidates"]) / 1000
        for turn in reviewers
    ]


#: A stand-in for the plan store's CLI, the published CLI the script asks for a plan's
#: documents: it answers each verb — `project show`, `project list`, `task list`, and a
#: `task list` resuming from a page — with the status and output `STORE_ANSWERS` scripts
#: for it, and a verb it was not scripted for exits 1, as an unreadable source would.
STORE_STAND_IN = """\
#!{python}
import json, os, sys
argv = sys.argv[1:]
verb = " ".join(argv[:2]) + (" page" if "--page" in argv else "")
answer = json.loads(os.environ["STORE_ANSWERS"]).get(verb, {{"status": 1, "stderr": "unscripted"}})
sys.stdout.write(answer.get("stdout", ""))
sys.stderr.write(answer.get("stderr", ""))
sys.exit(answer["status"])
"""

#: The script and what it sources, copied into a checkout of the stand-in's own.
SCRIPT_FILES = ("plan-checklist.sh", "llmlint-runtime-env.sh", "plan-brief.sh")


def _stand_in_checkout(tmp_path: Path, *, without: str = "") -> Path:
    """A checkout holding the script, its helpers and its configuration, over the stand-in.

    The interpreter is this suite's own; ``without`` names one file to leave out, as a
    checkout nothing bootstrapped, or one missing a helper, would.
    """
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "config").mkdir()
    (checkout / ".venv" / "bin").mkdir(parents=True)
    for name in SCRIPT_FILES:
        if name != without:
            shutil.copy(REPO_ROOT / "scripts" / name, checkout / "scripts" / name)
    shutil.copy(CONFIG, checkout / "config" / CONFIG.name)
    (checkout / ".venv" / "bin" / "python3").symlink_to(sys.executable)
    if without != "onetaskgraph":
        # The plan store is the published CLI the script delegates the question to, doubled
        # at that boundary and nothing above it: a store answering malformed JSON, a bad
        # cursor or an id-less listing cannot be produced by the real one on demand. The
        # script, its helpers, the interpreter and llmlint are all real.
        # llmlint: ignore-block[e2e_not_mocked] see above
        store = checkout / ".venv" / "bin" / "onetaskgraph"
        store.write_text(STORE_STAND_IN.format(python=sys.executable), encoding="utf-8")
        store.chmod(0o755)
        # llmlint: ignore-end[e2e_not_mocked]
    return checkout


def _answer(stdout: object = None, status: int = 0, stderr: str = "") -> dict[str, object]:
    text = stdout if isinstance(stdout, str) else json.dumps(stdout)
    return {"status": status, "stdout": "" if stdout is None else text, "stderr": stderr}


def _document(tmp_path: Path, relative: str) -> str:
    """A real file of an authoring root beside the checkout, by its absolute path."""
    path = tmp_path / "authoring" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntitle: P\n---\n", encoding="utf-8")
    return str(path.resolve())


def _items(*paths: str) -> dict[str, object]:
    return {"items": [{"item": {"location": {"path": path}}} for path in paths], "errors": []}


def _store_refusals(tmp_path: Path) -> list[tuple[str, dict[str, object], str]]:
    """Each store answer the script refuses, and the words its refusal names."""
    description = _document(tmp_path, "projects/plan.md")
    task = _document(tmp_path, "tasks/plan/route.md")
    # Named the way it would read as inside the root to a comparison of spellings alone.
    _document(tmp_path, "../elsewhere/tasks/route.md")
    elsewhere = str((tmp_path / "authoring").resolve()) + "/tasks/../../elsewhere/tasks/route.md"
    shown = _answer(_items(description))
    return [
        ("unreadable source", {}, "could not read the source of authoring:plan"),
        (
            "no such project",
            {"project list": _answer({"items": [{"item": {"id": "other"}}], "errors": []})},
            "the plan store holds no project authoring:plan",
        ),
        (
            "listed, not shown",
            {"project list": _answer({"items": [{"item": {"id": "plan"}}], "errors": []})},
            "lists authoring:plan but could not show it",
        ),
        (
            "an id-less listing",
            {"project list": _answer({"items": [{"item": {}}], "errors": []})},
            "listed a project of authoring with no id",
        ),
        ("no JSON", {"project show": _answer("not json")}, "with no JSON document"),
        ("no items", {"project show": _answer({"items": {}})}, "with no list of items"),
        (
            "store errors",
            {"project show": _answer({"items": [], "errors": ["down"]})},
            "with errors: ['down']",
        ),
        (
            "a cursor that is no string",
            {"project show": _answer({"items": [], "errors": [], "next": 3})},
            "a page cursor that is not a string",
        ),
        (
            "two descriptions",
            {"project show": _answer(_items(description, description))},
            "answered 2 records for the project authoring:plan",
        ),
        (
            "a relative location",
            {"project show": _answer(_items("projects/plan.md"))},
            "locates no file for the description",
        ),
        (
            "a missing file",
            {"project show": _answer(_items(description + ".gone"))},
            "locates no file for the description",
        ),
        (
            "a file the checklist does not select",
            {"project show": _answer(_items(_document(tmp_path, "projects/plan.txt")))},
            "which is not a Markdown document the checklist selects",
        ),
        (
            "no task list",
            {"project show": shown},
            "could not list the tasks of authoring:plan",
        ),
        (
            "a task outside the root",
            {"project show": shown, "task list": _answer(_items(task, elsewhere))},
            "are not under the authoring root",
        ),
        (
            "a failed second page",
            {"project show": shown, "task list": _answer(_items(task) | {"next": "2"})},
            "could not list the tasks of authoring:plan",
        ),
    ]


#: The cases `_store_refusals` builds, by name, so a case is selected without building any.
STORE_REFUSALS = (
    "unreadable source",
    "no such project",
    "listed, not shown",
    "an id-less listing",
    "no JSON",
    "no items",
    "store errors",
    "a cursor that is no string",
    "two descriptions",
    "a relative location",
    "a missing file",
    "a file the checklist does not select",
    "no task list",
    "a task outside the root",
    "a failed second page",
)


@pytest.mark.parametrize("case", STORE_REFUSALS)
def test_a_store_answer_the_script_cannot_trust_is_refused_naming_it(
    tmp_path: Path, checklist: Checklist, case: str
) -> None:
    """Every malformed or failing store answer refuses the review, and judges nothing."""
    cases = _store_refusals(tmp_path)
    assert [named for named, _, _ in cases] == list(STORE_REFUSALS)
    (answers, reason) = next((answers, reason) for named, answers, reason in cases if named == case)
    checkout = _stand_in_checkout(tmp_path)

    ran = subprocess.run(
        [str(checkout / "scripts" / "plan-checklist.sh"), "review", "authoring:plan"],
        cwd=checkout,
        env=checklist.environment | {"STORE_ANSWERS": json.dumps(answers)},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert reason in ran.stderr, ran.stderr
    assert checklist.llmlint_runs() == [], "a plan the store could not answer for was judged"


@pytest.mark.parametrize(
    ("missing", "reason"),
    [
        ("onetaskgraph", "is not installed; run 'just bootstrap'"),
        ("llmlint-runtime-env.sh", "llmlint-runtime-env.sh could not be loaded"),
        ("plan-brief.sh", "plan-brief.sh could not be loaded"),
    ],
)
def test_a_checkout_missing_what_the_script_needs_is_refused_naming_it(
    tmp_path: Path, checklist: Checklist, missing: str, reason: str
) -> None:
    checkout = _stand_in_checkout(tmp_path, without=missing)

    ran = subprocess.run(
        [str(checkout / "scripts" / "plan-checklist.sh"), "lint", "--cwd", "."],
        cwd=checkout,
        env=checklist.environment | _planning_run(tmp_path, PROJECT),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert reason in ran.stderr, ran.stderr


# The malformed plans these refusals are about are ones no engine writes on demand — two
# tasks in a planning run, a task with no text — so a record of that shape is written where
# the engine writes its own, which is the one way to reach the guard against one.
# llmlint: ignore-block[tests_mirror_real_usage] see above
def _one_task_plan(tmp_path: Path, tasks: object) -> dict[str, str]:
    """A planning run whose recorded plan holds ``tasks``, whatever shape they are."""
    planning = _planning_run(tmp_path, PROJECT)
    plan = Path(planning["ONEPIPELINE_RUNS_DIR"]) / planning["ONEPIPELINE_RUN_ID"] / "plan.json"
    plan.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    return planning


# llmlint: ignore-end[tests_mirror_real_usage]


@pytest.mark.parametrize(
    ("arguments", "environment", "reason"),
    [
        (["judge"], {}, "'judge' is neither of its two forms"),
        (["--version", "extra"], {}, "'--version' takes no other argument"),
        (["review", ":plan"], {}, "'review' needs a qualified plan project"),
        (["review", "authoring:"], {}, "'review' needs a qualified plan project"),
        (["lint", "--cwd", "."], {"ONEPIPELINE_RUN_ID": ""}, "no planning run is named"),
        (["lint", "--cwd", "."], {"ONEPIPELINE_RUN_ID": "../x"}, "which is not a run id"),
        (["lint", "--cwd", "."], {"ONEPIPELINE_RUN_ID": "absent"}, "has no readable plan"),
        (["lint", "--cwd", "."], {"tasks": [{"task": "a"}, {"task": "b"}]}, "holds no one task"),
        (["lint", "--cwd", "."], {"tasks": [{"id": "plan"}]}, "carries no task text"),
        (
            ["lint", "--cwd", "."],
            {"tasks": [{"task": "Plan project: a:b\nPlan project: c:d\n"}]},
            "names 2 plan projects",
        ),
        (["lint", "-c", "x"], {}, "names --cwd 0 time(s)"),
        (["lint", "--cwd", ".", "--cwd=."], {}, "names --cwd 2 time(s)"),
        (["lint", "--cwd"], {}, "'--cwd' names no directory"),
    ],
)
def test_an_invocation_the_script_cannot_serve_is_refused_naming_it(
    tmp_path: Path,
    checklist: Checklist,
    arguments: list[str],
    environment: dict[str, object],
    reason: str,
) -> None:
    """Neither of its two forms, or a planning run it cannot read a brief from: exit 2."""
    tasks = environment.pop("tasks", None)
    planning = _one_task_plan(tmp_path, tasks) if tasks is not None else {}
    named = {
        "ONEPIPELINE_RUNS_DIR": str(tmp_path / "runs"),
        "ONEPIPELINE_RUN_ID": "plan-journey",
        **planning,
        **{key: str(value) for key, value in environment.items()},
    }
    if tasks is None and named["ONEPIPELINE_RUN_ID"] == "plan-journey":
        named |= _planning_run(tmp_path, PROJECT)

    ran = subprocess.run(
        [str(SCRIPT), *arguments],
        cwd=tmp_path,
        env=checklist.environment | named,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert reason in ran.stderr, ran.stderr
    assert checklist.llmlint_runs() == [], "an invocation it could not serve reached llmlint"


def test_without_llmlint_on_path_the_script_says_how_to_install_it(
    tmp_path: Path, checklist: Checklist
) -> None:
    ran = subprocess.run(
        [str(SCRIPT), "--version"],
        cwd=tmp_path,
        env=checklist.environment | {"PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )

    assert ran.returncode == 2, ran.stdout + ran.stderr
    assert "llmlint is not installed on PATH; run 'just setup-llmlint'" in ran.stderr


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[shell_test_tiers_stay_split]
