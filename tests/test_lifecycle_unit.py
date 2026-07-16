"""Unit tests for lifecycle helpers, repo-plan loading/scheduling, and the CLIs."""

from __future__ import annotations

import json

import pytest

import orchestrator.lifecycle as lc
from orchestrator.config import ConfigError
from orchestrator.github import CliGitHubBackend, PullRequest
from orchestrator.lifecycle import (
    LifecycleResult,
    RepoPlan,
    RepoPlanNode,
    StackBase,
    Step,
    _default_body,
    _default_branch_name,
    _default_title,
    _effective_publication,
    _select_merge_strategy,
    _workstream_branch_name,
    load_repo_plan,
    make_repo_runner,
    run_repo_plan,
    run_repo_task,
)
from orchestrator.merge import GitHubMergeStrategy, LocalMergeStrategy
from orchestrator.plan import PlanError
from orchestrator.workspace import Workspace, normalize_repo


def _result(outcome: str, **kw) -> LifecycleResult:
    base = dict(repo="o/r", task="t", persona="p", base_branch="main", branch="b", outcome=outcome)
    base.update(kw)
    return LifecycleResult(**base)


# --- helpers ---------------------------------------------------------------


def test_branch_name_is_deterministic() -> None:
    a = _default_branch_name("backend-engineer", "do a thing")
    assert a == _default_branch_name("backend-engineer", "do a thing")
    assert a.startswith("ai-orchestrator/backend-engineer/")


def test_default_title_truncates_long_first_line() -> None:
    title = _default_title("p", "x" * 200)
    assert title.endswith("…") and len(title) <= 69


def test_default_body_mentions_persona_and_turns() -> None:
    from orchestrator.dispatch import Report

    report = Report("p", 0, True, False, 3, [], {}, {}, "")
    body = _default_body("reviewer", "Do the thing.", report)
    assert "reviewer" in body and "3 agent turn" in body and "## What" in body


def test_summary_includes_pr_and_gate() -> None:
    from orchestrator.dispatch import Report
    from orchestrator.verify import VerifyResult

    result = _result(
        "gate-failed",
        pr=PullRequest(7, "u", "o/r", "b", "main"),
        report=Report("p", 0, True, False, 1, [], {}, {}, "", "- Add an edge-case test."),
        verify=VerifyResult(False, ["just", "check"], "boom"),
        detail="local gate failed",
    )
    text = result.summary()
    assert "PR #7" in text and "gate:" in text and "local gate failed" in text
    assert "follow-ups: - Add an edge-case test." in text


def test_select_merge_strategy() -> None:
    local = normalize_repo("/tmp")
    assert isinstance(_select_merge_strategy(local, None, None), GitHubMergeStrategy)
    remote = normalize_repo("o/r")
    assert isinstance(_select_merge_strategy(remote, None, CliGitHubBackend()), GitHubMergeStrategy)
    assert isinstance(_select_merge_strategy(remote, None, None), GitHubMergeStrategy)
    assert isinstance(_select_merge_strategy(remote, None, None, "local"), LocalMergeStrategy)
    assert isinstance(_select_merge_strategy(local, None, None, "remote"), GitHubMergeStrategy)
    explicit = LocalMergeStrategy()
    assert _select_merge_strategy(remote, explicit, None) is explicit


@pytest.mark.parametrize(
    "repo_type, workflow, policy, expected",
    [
        ("single-owner", "local", None, ("local", "direct")),
        ("single-owner", "local", "auto", ("local", "direct")),
        ("single-owner", "local", "direct", ("local", "direct")),
        ("single-owner", "remote", None, ("remote", "auto")),
        ("single-owner", "remote", "auto", ("remote", "auto")),
        ("single-owner", "remote", "direct", ("remote", "direct")),
        ("team", "remote", None, ("remote", "none")),
        ("team", "remote", "auto", ("remote", "auto")),
        ("team", "remote", "direct", ("remote", "direct")),
        ("team", "remote", "none", ("remote", "none")),
        ("team", "local", None, ("remote", "none")),
        ("team", "local", "auto", ("remote", "auto")),
        ("team", "local", "direct", ("remote", "direct")),
        ("team", "local", "none", ("remote", "none")),
        ("single-owner", "local", "none", ("remote", "none")),
        ("single-owner", "remote", "none", ("remote", "none")),
    ],
)
def test_repository_type_policy_matrix(repo_type, workflow, policy, expected) -> None:
    decision = _effective_publication(repo_type, workflow, None, policy)
    assert (decision.workflow, decision.merge_policy) == expected


def test_team_run_override_normalizes_stored_local_workflow() -> None:
    decision = _effective_publication("team", "local", None, None)
    assert (decision.workflow, decision.merge_policy) == ("remote", "none")


def test_team_explicit_local_workflow_is_invalid() -> None:
    with pytest.raises(ValueError, match="team.*workflow=local"):
        _effective_publication("team", None, "local", None)


@pytest.mark.parametrize(
    "anchor, expected",
    [
        (StackBase("parent", repo="https://github.com/o/r"), "normalized owner/name"),
        (StackBase("parent", identity="git@github.com:o/r.git"), "normalized origin"),
        (
            StackBase(
                "parent",
                repo="o/r",
                identity="https://github.com/o/x",
            ),
            "does not match identity",
        ),
        (StackBase("parent", pr="https://example.com/pull/1"), "pull-request URL"),
        (
            StackBase(
                "parent",
                repo="o/r",
                pr="https://github.com/x/r/pull/1",
            ),
            "does not match repo",
        ),
        (
            StackBase(
                "parent",
                identity="https://github.com/o/r",
                pr="https://github.com/o/x/pull/1",
            ),
            "does not match identity",
        ),
    ],
)
def test_programmatic_stack_anchors_fail_before_repository_resolution(
    tmp_path, anchor, expected
) -> None:
    result = run_repo_task(
        "o/r",
        "task",
        "backend-engineer",
        workspace=Workspace(tmp_path / "unused", resolver=lambda _spec: tmp_path / "missing"),
        stack_bases=[anchor],
    )
    assert result.outcome == "error" and expected in result.detail


# --- repo-plan loading -----------------------------------------------------


def _write(tmp_path, obj: dict) -> str:
    p = tmp_path / "repo-plan.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_load_valid_repo_plan(tmp_path) -> None:
    plan = load_repo_plan(
        _write(
            tmp_path,
            {
                "concurrency": 2,
                "tasks": [
                    {"id": "a", "repo": "o/r", "persona": "backend-engineer", "task": "A"},
                    {
                        "id": "b",
                        "repo": "o/r",
                        "persona": "reviewer",
                        "task": "B",
                        "deps": ["a"],
                        "merge_policy": "direct",
                        "workflow": "local",
                        "repo_type": "single-owner",
                        "stack_bases": [
                            {
                                "branch": "feature/parent",
                                "repo": "o/r",
                                "identity": "https://github.com/o/r",
                                "base_branch": "main",
                                "pr": "https://github.com/o/r/pull/1",
                            }
                        ],
                        "skip_verify": True,
                    },
                ],
            },
        )
    )
    assert plan.concurrency == 2
    assert [t.id for t in plan.tasks] == ["a", "b"]
    assert plan.tasks[1].merge_policy == "direct" and plan.tasks[1].skip_verify
    assert plan.tasks[1].workflow == "local"
    assert plan.tasks[1].repo_type == "single-owner"
    assert plan.tasks[1].stack_bases[0].branch == "feature/parent"


@pytest.mark.parametrize(
    "obj, match",
    [
        ({"tasks": []}, "non-empty 'tasks'"),
        (
            {"concurrency": 0, "tasks": [{"id": "a", "repo": "r", "persona": "p", "task": "t"}]},
            "concurrency",
        ),
        ({"tasks": ["x"]}, "must be a mapping"),
        ({"tasks": [{"repo": "r", "persona": "p", "task": "t"}]}, "non-empty string 'id'"),
        ({"tasks": [{"id": "a", "persona": "p", "task": "t"}]}, "non-empty 'repo'"),
        ({"tasks": [{"id": "a", "repo": "r", "task": "t"}]}, "non-empty 'persona'"),
        ({"tasks": [{"id": "a", "repo": "r", "persona": "p"}]}, "non-empty 'task'"),
        (
            {"tasks": [{"id": "a", "repo": "r", "persona": "p", "task": "t", "deps": "x"}]},
            "list of ids",
        ),
        (
            {"tasks": [{"id": "a", "repo": "r", "persona": "p", "task": "t", "merge_policy": "x"}]},
            "merge_policy",
        ),
        (
            {"tasks": [{"id": "a", "repo": "r", "persona": "p", "task": "t", "workflow": "x"}]},
            "workflow",
        ),
        (
            {"tasks": [{"id": "a", "repo": "r", "persona": "p", "task": "t", "repo_type": "x"}]},
            "repo_type",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "repo_type": "team",
                        "workflow": "local",
                    }
                ]
            },
            "team.*workflow=local",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "branch": "bad..branch",
                    }
                ]
            },
            "branch.*valid.*Git branch",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "base_branch": 3,
                    }
                ]
            },
            "base_branch.*valid.*Git branch",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [{"repo": "r"}],
                    }
                ]
            },
            "stack_bases",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "execution_checkout": "",
                    }
                ]
            },
            "execution_checkout",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": "branch",
                    }
                ]
            },
            "stack_bases",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": ["branch"],
                    }
                ]
            },
            "must be a mapping",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [{"branch": "parent", "unexpected": "x"}],
                    }
                ]
            },
            "unknown fields",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [{"branch": "parent", "pr": ""}],
                    }
                ]
            },
            "must be a non-empty string",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [{"branch": "invalid..branch"}],
                    }
                ]
            },
            "not a valid Git branch",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [
                            {
                                "branch": "feature/parent",
                                "identity": "git@github.com:o/r.git",
                            }
                        ],
                    }
                ]
            },
            "not a normalized origin",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [
                            {
                                "branch": "feature/parent",
                                "repo": "o/r",
                                "identity": "https://github.com/o/x",
                            }
                        ],
                    }
                ]
            },
            "repo.*does not match 'identity'",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [
                            {
                                "branch": "feature/parent",
                                "repo": "https://github.com/o/r",
                            }
                        ],
                    }
                ]
            },
            "normalized owner/name",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [{"branch": "feature/parent", "base_branch": "bad..base"}],
                    }
                ]
            },
            "base_branch.*not a valid Git branch",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [{"branch": "feature/parent", "pr_base": "bad..base"}],
                    }
                ]
            },
            "pr_base.*not a valid Git branch",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [
                            {"branch": "feature/parent", "pr": "https://example.com/1"}
                        ],
                    }
                ]
            },
            "pull-request URL",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [
                            {
                                "branch": "feature/parent",
                                "repo": "o/r",
                                "pr": "https://github.com/x/r/pull/1",
                            }
                        ],
                    }
                ]
            },
            "pr.*does not match 'repo'",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "stack_bases": [
                            {
                                "branch": "feature/parent",
                                "identity": "https://github.com/o/r",
                                "pr": "https://github.com/o/x/pull/1",
                            }
                        ],
                    }
                ]
            },
            "pr.*does not match 'identity'",
        ),
        (
            {"tasks": [{"id": "a", "repo": "r", "persona": "p", "task": "t", "deps": ["z"]}]},
            "unknown task",
        ),
        (
            {"tasks": [{"id": "a", "repo": "r", "persona": "p", "task": "t", "deps": ["a"]}]},
            "depends on itself",
        ),
        (
            {
                "tasks": [
                    {"id": "a", "repo": "r", "persona": "p", "task": "t"},
                    {"id": "a", "repo": "r", "persona": "p", "task": "t"},
                ]
            },
            "duplicate",
        ),
    ],
)
def test_load_repo_plan_rejects(tmp_path, obj, match) -> None:
    with pytest.raises(PlanError, match=match):
        load_repo_plan(_write(tmp_path, obj))


def test_load_repo_plan_rejects_cycle(tmp_path) -> None:
    with pytest.raises(PlanError, match="cycle"):
        load_repo_plan(
            _write(
                tmp_path,
                {
                    "tasks": [
                        {"id": "a", "repo": "r", "persona": "p", "task": "t", "deps": ["b"]},
                        {"id": "b", "repo": "r", "persona": "p", "task": "t", "deps": ["a"]},
                    ]
                },
            )
        )


def test_load_repo_plan_missing_file(tmp_path) -> None:
    with pytest.raises(PlanError):
        load_repo_plan(tmp_path / "nope.json")


# --- workstreams (steps sub-DAG on one PR) --------------------------------


def test_load_repo_plan_with_steps(tmp_path) -> None:
    plan = load_repo_plan(
        _write(
            tmp_path,
            {
                "tasks": [
                    {
                        "id": "feature",
                        "repo": "o/r",
                        "steps": [
                            {"id": "impl", "persona": "backend-engineer", "task": "build"},
                            {
                                "id": "test",
                                "persona": "test-engineer",
                                "task": "cover",
                                "deps": ["impl"],
                                "max_turns": 6,
                            },
                        ],
                    }
                ]
            },
        )
    )
    node = plan.tasks[0]
    assert node.persona is None and node.task is None
    assert node.steps is not None and [s.id for s in node.steps] == ["impl", "test"]
    assert node.steps[1].deps == ["impl"] and node.steps[1].max_turns == 6


@pytest.mark.parametrize(
    "steps, match",
    [
        ("not-a-list", "'steps' must be a non-empty list"),
        ([], "'steps' must be a non-empty list"),
        (["x"], "step #0 must be a mapping"),
        ([{"persona": "p", "task": "t"}], "step #0 needs a non-empty string 'id'"),
        ([{"id": "s", "task": "t"}], "step 's' needs a non-empty 'persona'"),
        ([{"id": "s", "persona": "p"}], "step 's' needs a non-empty 'task'"),
        ([{"id": "s", "persona": "p", "task": "t", "deps": "x"}], "'deps' must be a list of ids"),
        ([{"id": "s", "persona": "p", "task": "t", "deps": ["z"]}], "depends on unknown step"),
        ([{"id": "s", "persona": "p", "task": "t", "deps": ["s"]}], "depends on itself"),
        (
            [{"id": "a", "persona": "p", "task": "t"}, {"id": "a", "persona": "p", "task": "t"}],
            "duplicate step id",
        ),
        (
            [
                {"id": "a", "persona": "p", "task": "t", "deps": ["b"]},
                {"id": "b", "persona": "p", "task": "t", "deps": ["a"]},
            ],
            "cycle",
        ),
    ],
)
def test_load_repo_plan_rejects_bad_steps(tmp_path, steps, match) -> None:
    with pytest.raises(PlanError, match=match):
        load_repo_plan(_write(tmp_path, {"tasks": [{"id": "f", "repo": "o/r", "steps": steps}]}))


def test_load_repo_plan_node_needs_persona_task_or_steps(tmp_path) -> None:
    with pytest.raises(PlanError, match="needs a non-empty 'persona' .or a 'steps' list."):
        load_repo_plan(_write(tmp_path, {"tasks": [{"id": "a", "repo": "o/r"}]}))


def test_run_repo_task_requires_task_or_steps(tmp_path) -> None:
    with pytest.raises(ConfigError, match="needs either"):
        run_repo_task("o/r", workspace=Workspace(tmp_path / "ws"))


def test_workstream_branch_name_unique_and_task_identifiable() -> None:
    a = _workstream_branch_name([Step("impl", "backend-engineer", "x")])
    assert a != _workstream_branch_name([Step("impl", "backend-engineer", "x")])
    assert "/979f9ea4-" in a
    b = _workstream_branch_name(
        [Step("impl", "backend-engineer", "x"), Step("test", "test-engineer", "y")]
    )
    assert a != b and a.startswith("ai-orchestrator/backend-engineer/")


def test_summary_shows_step_count() -> None:
    from orchestrator.lifecycle import StepResult

    result = _result(
        "merged",
        steps=[StepResult("a", "p", "done"), StepResult("b", "q", "done")],
    )
    assert "[2/2 steps]" in result.summary()


# --- scheduling ------------------------------------------------------------


def test_run_repo_plan_cascades(tmp_path) -> None:
    def runner(node: RepoPlanNode) -> LifecycleResult:
        return _result("merged" if node.id != "b" else "gate-failed", repo=node.repo)

    plan = RepoPlan(
        tasks=[
            RepoPlanNode("a", "o/r", "p", "t"),
            RepoPlanNode("b", "o/r", "p", "t"),
            RepoPlanNode("c", "o/r", "p", "t", deps=["b"]),
        ],
        concurrency=3,
    )
    result = run_repo_plan(plan, runner)
    assert result.results["a"].status == "done"
    assert result.results["b"].status == "failed"
    assert result.results["c"].status == "skipped"
    assert not result.ok
    assert "repo-plan" in result.summary()


def test_run_repo_plan_turns_open_same_identity_dependency_into_stack_base() -> None:
    seen: dict[str, list[StackBase]] = {}

    def runner(node: RepoPlanNode) -> LifecycleResult:
        seen[node.id] = node.stack_bases
        if node.id == "parent":
            return _result(
                "pr-open",
                repo=node.repo,
                branch="feature/parent",
                pr_base="main",
                publication_identity="https://github.com/o/r",
                pr=PullRequest(1, "https://github.com/o/r/pull/1", "o/r", "feature/parent", "main"),
            )
        return _result("pr-open", repo=node.repo, pr_base="feature/parent")

    result = run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode("parent", "o/r", "p", "parent"),
                RepoPlanNode("child", "o/r", "p", "child", deps=["parent"]),
            ]
        ),
        runner,
    )

    assert result.ok
    assert seen["child"] == [
        StackBase(
            "feature/parent",
            repo="o/r",
            identity="https://github.com/o/r",
            base_branch="main",
            pr="https://github.com/o/r/pull/1",
            pr_base="main",
        )
    ]


def test_run_repo_plan_carries_landed_nonroot_base() -> None:
    seen: dict[str, list[StackBase]] = {}

    def runner(node: RepoPlanNode) -> LifecycleResult:
        seen[node.id] = node.stack_bases
        if node.id == "parent":
            return _result(
                "merged",
                repo=node.repo,
                pr_base="ai-orchestrator/stack-base/x",
                publication_identity="identity",
            )
        return _result("merged", repo=node.repo, pr_base="main")

    run_repo_plan(
        RepoPlan(
            [
                RepoPlanNode("parent", "o/r", "p", "parent"),
                RepoPlanNode("child", "o/r", "p", "child", deps=["parent"]),
            ]
        ),
        runner,
    )
    assert seen["child"][0].branch == "ai-orchestrator/stack-base/x"


def test_make_repo_runner_threads_node_fields(monkeypatch) -> None:
    captured = {}

    def fake_task(repo, task, persona, **kw):
        captured.update(kw, repo=repo, task=task, persona=persona)
        return _result("merged", repo=repo)

    monkeypatch.setattr(lc, "run_repo_task", fake_task)
    runner = make_repo_runner(
        workspace=Workspace("/tmp/ws"),
        base_path="base.yaml",
        persona_dir="personas",
        merge_policy="auto",
        merge_method="squash",
        oneharness_mode="bypass",
        skip_verify=False,
        poll_interval=1.0,
        timeout=9.0,
        repo_type="team",
    )
    node = RepoPlanNode(
        "n",
        "o/r",
        "reviewer",
        "task",
        merge_policy="direct",
        skip_verify=True,
        repo_type="single-owner",
    )
    out = runner(node)
    assert out.outcome == "merged"
    assert captured["repo"] == "o/r" and captured["persona"] == "reviewer"
    assert captured["merge_policy"] == "direct"  # node override wins
    assert captured["repo_type"] == "single-owner"
    assert captured["skip_verify"] is True
    assert captured["oneharness_mode"] == "bypass"


# --- error path ------------------------------------------------------------


def test_run_repo_task_git_error_is_reported(tmp_path) -> None:
    result = run_repo_task(
        "acme/widget",
        "t",
        "backend-engineer",
        workspace=Workspace(tmp_path / "ws"),
        url=str(tmp_path / "does-not-exist.git"),  # clone fails → GitError
        verify_cmd=["true"],
    )
    assert result.outcome == "error" and not result.ok and result.detail


# --- CLIs ------------------------------------------------------------------


def test_main_task_json_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        lc,
        "run_repo_task",
        lambda *a, **k: _result("merged", pr=PullRequest(1, "url", "o/r", "b", "main")),
    )
    rc = lc.main_task(["acme/widget", "backend-engineer", "do it", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "merged" and payload["pr"] == "url"


def test_main_task_rejects_nonpositive_publication_attempts(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        lc.main_task(["acme/widget", "backend-engineer", "do it", "--publication-attempts", "0"])
    assert exc.value.code == 2
    assert "must be at least 1" in capsys.readouterr().err


def test_main_task_rejects_invalid_literal_branch_before_resolution(capsys) -> None:
    rc = lc.main_task(
        [
            "acme/widget",
            "backend-engineer",
            "do it",
            "--branch",
            "bad..branch",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1 and payload["outcome"] == "error"
    assert "not a valid Git branch" in payload["detail"]


def test_main_task_human_nonzero_on_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(lc, "run_repo_task", lambda *a, **k: _result("gate-failed"))
    rc = lc.main_task(["acme/widget", "backend-engineer", "do it"])
    assert rc == 1
    assert "gate-failed" in capsys.readouterr().out


def test_main_task_writes_output_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(lc, "run_repo_task", lambda *a, **k: _result("merged"))
    out = tmp_path / "r.json"
    rc = lc.main_task(["acme/widget", "reviewer", "do it", "--format", "json", "-o", str(out)])
    assert rc == 0 and '"outcome"' in out.read_text(encoding="utf-8")


def test_main_task_reads_task_from_stdin(monkeypatch, capsys) -> None:
    import io

    seen = {}

    def fake_task(repo, task, persona, **kw):
        seen["task"] = task
        return _result("merged")

    monkeypatch.setattr(lc, "run_repo_task", fake_task)
    monkeypatch.setattr("sys.stdin", io.StringIO("task via stdin"))
    rc = lc.main_task(["acme/widget", "backend-engineer"])  # task omitted → stdin
    assert rc == 0 and seen["task"] == "task via stdin"


def test_main_plan_bad_plan_exit_2(tmp_path, capsys) -> None:
    rc = lc.main_plan([str(tmp_path / "missing.json")])
    assert rc == 2 and "repo-plan:" in capsys.readouterr().err


def test_main_plan_happy(monkeypatch, tmp_path, capsys) -> None:
    from orchestrator.lifecycle import RepoPlanResult, RepoTaskResult

    plan_file = _write(
        tmp_path, {"tasks": [{"id": "a", "repo": "o/r", "persona": "reviewer", "task": "t"}]}
    )
    monkeypatch.setattr(
        lc,
        "run_repo_plan",
        lambda plan, runner, concurrency=None: RepoPlanResult(
            results={"a": RepoTaskResult("a", "done", result=_result("merged"))},
            started_order=["a"],
        ),
    )
    rc = lc.main_plan([plan_file, "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["results"]["a"]["outcome"] == "merged"


def test_main_plan_human_format(monkeypatch, tmp_path, capsys) -> None:
    from orchestrator.lifecycle import RepoPlanResult, RepoTaskResult

    plan_file = _write(
        tmp_path, {"tasks": [{"id": "a", "repo": "o/r", "persona": "reviewer", "task": "t"}]}
    )
    monkeypatch.setattr(
        lc,
        "run_repo_plan",
        lambda plan, runner, concurrency=None: RepoPlanResult(
            results={"a": RepoTaskResult("a", "failed", result=_result("checks-failed"))},
            started_order=["a"],
        ),
    )
    rc = lc.main_plan([plan_file])  # human format (default)
    assert rc == 1  # not ok
    assert "repo-plan" in capsys.readouterr().out
