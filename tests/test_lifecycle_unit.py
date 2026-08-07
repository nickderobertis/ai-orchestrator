"""Unit tests for lifecycle helpers, repo-plan loading/scheduling, and the CLIs."""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path

import pytest
from conftest import install_pre_push_hook

import orchestrator.graph as graph
import orchestrator.lifecycle as lc
from orchestrator.config import ConfigError
from orchestrator.github import CliGitHubBackend, PRStatus, PullRequest
from orchestrator.lifecycle import (
    AI_ORCHESTRATOR_IDENTITY,
    PR_OPTIONAL_SECTIONS,
    PR_REQUIRED_SECTIONS,
    TASK_OPTIONAL_SECTIONS,
    TASK_REQUIRED_SECTIONS,
    LifecycleResult,
    RepoPlan,
    RepoPlanNode,
    Resume,
    RetryLineage,
    StackBase,
    Step,
    _default_body,
    _default_branch_name,
    _draft_pr_body,
    _effective_publication,
    _incomplete_commit_message,
    _select_merge_strategy,
    _should_draft_pr_body,
    _subject_from_messages,
    _valid_drafted_body,
    _workstream_branch_name,
    load_repo_plan,
    make_repo_runner,
    run_repo_plan,
    run_repo_task,
)
from orchestrator.merge import GitHubMergeStrategy, LocalMergeStrategy
from orchestrator.plan import PlanError
from orchestrator.provenance import (
    INCOMPLETE_TRAILER,
    RECOVERY_TRAILER,
    PreservedStepMetadata,
    format_preserved_step_metadata,
    parse_preserved_step_metadata,
)
from orchestrator.recover import RecoveryResult
from orchestrator.registry import Registry
from orchestrator.runs import ResumePayload, RetryLineagePayload
from orchestrator.workspace import Workspace, normalize_repo


def _result(outcome: str, **kw) -> LifecycleResult:
    base = dict(repo="o/r", task="t", persona="p", base_branch="main", branch="b", outcome=outcome)
    base.update(kw)
    return LifecycleResult(**base)


def test_resume_and_retry_lineage_payload_contracts_cannot_drift() -> None:
    """Every dataclass field must have a serialized boundary field and vice versa."""
    assert {field.name for field in fields(Resume)} == (
        ResumePayload.__required_keys__ | ResumePayload.__optional_keys__
    )
    assert {field.name for field in fields(RetryLineage)} == (
        RetryLineagePayload.__required_keys__ | RetryLineagePayload.__optional_keys__
    )


@pytest.mark.reads_docs
def test_documented_llmlint_wrapper_identity_matches_routing_constant() -> None:
    documentation = (Path(__file__).parents[1] / "docs" / "repo-lifecycle.md").read_text(
        encoding="utf-8"
    )
    assert f"identity is `{AI_ORCHESTRATOR_IDENTITY}`" in documentation


def test_preserved_step_metadata_contract_round_trips() -> None:
    metadata = format_preserved_step_metadata("repair-1", "backend")

    assert parse_preserved_step_metadata(
        f"subject\n\n{metadata}, preserved by orchestrator"
    ) == PreservedStepMetadata(
        step_id="repair-1",
        persona="backend",
    )


# --- helpers ---------------------------------------------------------------


def test_branch_name_is_deterministic() -> None:
    a = _default_branch_name("engineer", "do a thing")
    assert a == _default_branch_name("engineer", "do a thing")
    assert a.startswith("ai-orchestrator/engineer/")


def test_pr_body_drafting_reads_output_and_falls_back(tmp_path) -> None:
    from orchestrator.dispatch import Report
    from orchestrator.journal import NullNodeJournal

    calls: list[str] = []

    def drafting_dispatch(persona: str, task: str, **_: object) -> Report:
        calls.append(persona)
        output = task.split("Write the final body, and nothing else, to this absolute path:\n", 1)[
            1
        ].splitlines()[0]
        Path(output).write_text("## What\nA behavior.\n\n## Why\nA driver.\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    common = dict(
        worktree=tmp_path,
        remote_base="origin/main",
        steps=[Step("change", "engineer", "Raw handoff prose")],
        fallback="fallback",
        oneharness_mode="bypass",
        base_path="base.yaml",
        persona_dir="personas",
        journal=NullNodeJournal(),
        dispatch_env={},
    )
    body = _draft_pr_body(dispatch_fn=drafting_dispatch, **common)
    assert body == "## What\nA behavior.\n\n## Why\nA driver.\n"
    assert calls == ["pr-author"]

    def failed_dispatch(*_: object, **__: object) -> Report:
        raise RuntimeError("provider unavailable")

    assert _draft_pr_body(dispatch_fn=failed_dispatch, **common) == "fallback"


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        (None, None, True),
        ("feat: supplied", None, True),
        (None, "supplied", False),
        ("feat: supplied", "supplied", False),
    ],
)
def test_only_explicit_pr_body_skips_body_drafting(title, body, expected) -> None:
    assert _should_draft_pr_body(title, body) is expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("## What\nBehavior.\n\n## Why\nDriver.", True),
        ("## What\nBehavior.\n\n## Why\nDriver.\n\n## Additional info\nNote.", True),
        ("arbitrary prose", False),
        ("## What\n\n## Why\nDriver.", False),
        ("## Why\nDriver.\n\n## What\nBehavior.", False),
    ],
)
def test_drafted_body_validation(body, expected) -> None:
    assert _valid_drafted_body(body) is expected


@pytest.mark.reads_docs
def test_documented_branch_discovered_continuation_fields_track_the_producer(
    tmp_path, bare_origin
) -> None:
    """The prose names the continuation fields; the lifecycle decides what they are.

    `docs/repo-lifecycle.md` tells a planner how to read `branch-discovered` — which
    field says work was adopted, which names the commit, which carries the reason it
    was not. That is a restatement of the event, so it needs a gate: the names come
    from an event this run actually emitted, and each has to be documented. Renaming
    or dropping one in the producer fails here rather than leaving the prose
    describing a field nobody writes.
    """
    from orchestrator import gitops
    from orchestrator.dispatch import Report
    from orchestrator.journal import NodeJournal, open_journal
    from orchestrator.runs import NodeId, RunId

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "canonical-branch-discovered")
    install_pre_push_hook(publication)

    def writing_dispatch(persona, task, *, project_dir, **_kw):
        Path(project_dir, "documented.txt").write_text("documented\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    workspace = Workspace(
        tmp_path / "ws-branch-discovered",
        resolver=lambda _url: publication,
        workflow="local",
        repo_type="single-owner",
    )
    journal = open_journal(tmp_path / "run-bd", RunId("run-bd"), 1)
    result = run_repo_task(
        str(origin),
        "Document the continuation fields.",
        "engineer",
        workspace=workspace,
        recorded_gate=["true"],
        dispatch_fn=writing_dispatch,
        journal=NodeJournal(sink=journal, node=NodeId("api"), run_id=RunId("run-bd"), round=1),
    )
    assert result.outcome == "merged", result.detail

    discovered = next(event for event in journal.events() if event.kind == "branch-discovered")
    continuation = {"resumed", "resumed_from", "resume_declined"}
    assert continuation <= set(discovered.detail), discovered.detail
    docs = (Path(__file__).parents[1] / "docs/repo-lifecycle.md").read_text(encoding="utf-8")
    for field_name in sorted(continuation):
        assert f"`{field_name}`" in docs


@pytest.mark.reads_docs
def test_pr_author_contract_tracks_checked_in_template_persona_and_docs() -> None:
    """Make intentional contract copies fail together when the template changes."""
    root = Path(__file__).parents[1]
    template = (root / ".github/pull_request_template.md").read_text(encoding="utf-8")
    persona = (root / "personas/pr-author.yaml").read_text(encoding="utf-8")
    docs = (root / "docs/repo-lifecycle.md").read_text(encoding="utf-8")
    template_sections = re.findall(r"(?m)^## ([^\n]+)$", template)
    assert template_sections == [*PR_REQUIRED_SECTIONS, *PR_OPTIONAL_SECTIONS]
    for section in template_sections:
        assert section in persona
        assert section in docs


@pytest.mark.reads_docs
def test_task_prose_contract_tracks_docs_and_agent_tasks_in_example() -> None:
    """Every shipped example's task prose is the sequence `lifecycle` parses by.

    Globbed rather than listed, for the same reason the schema-version gate is: an
    example added later is a file an operator copies, so it has to track
    `TASK_REQUIRED_SECTIONS` without anyone remembering to name it here.
    """
    root = Path(__file__).parents[1]
    docs = (root / "docs/orchestration.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    expected = [*TASK_REQUIRED_SECTIONS, *TASK_OPTIONAL_SECTIONS]
    for section in expected:
        assert f"## {section}" in docs
        assert f"`## {section}`" in agents or section == "Additional info"

    examples = sorted((root / "examples").glob("*.json"))
    assert len(examples) >= 5, examples
    for path in examples:
        example = json.loads(path.read_text(encoding="utf-8"))
        agent_tasks = []
        for node in example["tasks"]:
            if node.get("kind", "agent") == "agent":
                agent_tasks.extend(
                    step["task"]
                    for step in node.get("steps", [])
                    if step.get("kind", "agent") == "agent"
                )
                if "task" in node and "steps" not in node:
                    agent_tasks.append(node["task"])
        assert agent_tasks, path.name
        for task in agent_tasks:
            headings = re.findall(r"(?m)^## ([^\n]+)$", task)
            assert headings[: len(TASK_REQUIRED_SECTIONS)] == list(TASK_REQUIRED_SECTIONS), (
                path.name
            )
            assert headings[len(TASK_REQUIRED_SECTIONS) :] in (
                [],
                list(TASK_OPTIONAL_SECTIONS),
            ), path.name


def _commit_messages(*messages: str) -> list[lc.gitops.CommitMessage]:
    return [lc.gitops.CommitMessage(str(index), message) for index, message in enumerate(messages)]


def test_single_commit_subject_becomes_default_title() -> None:
    title = _subject_from_messages(
        _commit_messages("fix(capture): preserve failed session output"), "irrelevant task prose"
    )
    assert title == "fix(capture): preserve failed session output"


def test_multiple_commit_subjects_name_the_most_significant_change_alone() -> None:
    """A squash subject names the change; the branch's own history keeps its steps."""
    title = _subject_from_messages(
        _commit_messages(
            "docs: explain session capture",
            "fix: retain failed output",
            "feat: expose captured sessions",
            "perf: avoid duplicate reads",
        ),
        "irrelevant task prose",
    )
    assert title == "feat: expose captured sessions"
    assert "explain session capture" not in title and "retain failed output" not in title


def test_breaking_commit_signal_survives_synthesis() -> None:
    title = _subject_from_messages(
        _commit_messages(
            "feat: add capture metadata",
            "refactor(api): replace session result\n\nBREAKING CHANGE: callers must read output",
        ),
        "irrelevant task prose",
    )
    assert title.startswith("refactor!:")
    parsed = lc._parse_conventional_subject(title)
    assert parsed is not None and parsed.breaking


def test_breaking_footer_survives_when_no_subject_is_usable() -> None:
    title = _subject_from_messages(
        _commit_messages("Update API\n\nBREAKING CHANGE: old API removed"),
        "Describe the API migration.",
    )
    assert title == "chore!: Describe the API migration."


def test_breaking_footer_on_invalid_subject_marks_mixed_history() -> None:
    title = _subject_from_messages(
        _commit_messages(
            "fix: retain output",
            "Update API\n\nBREAKING CHANGE: old API removed",
        ),
        "irrelevant task prose",
    )
    assert title == "fix!: retain output"


def test_a_description_too_long_for_the_limit_falls_back_to_the_task_name() -> None:
    """A description is published whole or not at all — never cut to fit.

    The scope is a Conventional Commit guarantee rather than room to trade away, so it
    survives the fall-through with the type and the breaking marker.
    """
    title = _subject_from_messages(
        _commit_messages(f"fix(capture): {'x' * 200}"), "## What\nRetain failed session output.\n"
    )
    assert title == "fix(capture): Retain failed session output."
    assert "x" * 20 not in title


@pytest.mark.parametrize(
    "messages, task",
    [
        # Neither the commit descriptions nor the task prose fits behind the prefix.
        ((f"feat: {'x' * 200}", f"fix: {'y' * 200}"), f"## What\n{'z' * 200}\n"),
        ((f"fix(capture): {'x' * 200}",), f"## What\n{'y' * 200}\n"),
        ((), f"## What\n{'z' * 200}\n"),
        # Prose that names nothing summarizes as the generic description, which is not a
        # name for a change and so is not a candidate for one.
        ((), "## What\n\n## Why\n"),
        # A scope stays whole, so a description that would only fit without it does not.
        ((f"fix({'s' * 40}): retain the captured output of a failed session",), "## What\n\n"),
    ],
    ids=["all-commits", "scoped-commit", "no-commit", "unnamed-prose", "scope-crowds-description"],
)
def test_a_subject_that_cannot_be_formed_is_refused(messages: tuple[str, ...], task: str) -> None:
    # Publishing `chore: orchestrated change` names the change no better than a subject
    # cut off mid-word does, so the caller hears the limit and the two ways out instead.
    with pytest.raises(ConfigError, match=f"at most {lc._SUBJECT_LIMIT} characters"):
        _subject_from_messages(_commit_messages(*messages), task)


def test_a_refusal_names_the_prefix_and_the_ways_out() -> None:
    with pytest.raises(ConfigError) as refusal:
        _subject_from_messages(_commit_messages(f"feat(capture)!: {'x' * 200}"), "## What\n\n")
    message = str(refusal.value)
    assert "feat(capture)!:" in message
    assert "shorten a commit subject" in message and "explicit title" in message


def test_a_subject_the_limit_cannot_hold_at_all_is_rejected() -> None:
    # No description fits behind a prefix this long, so there is nothing to publish and
    # the caller hears so rather than receiving a subject without a description.
    with pytest.raises(ConfigError):
        lc._format_conventional_subject(lc._ParsedSubject("t" * 70, None, False, "a change"))


@pytest.mark.parametrize(
    "messages, task",
    [
        ((), "Task prose is not a commit title."),
        (("not a conventional subject",), "Fallback description."),
        (("chore: refresh fixtures",), "Ignored task."),
        (("security: harden token parsing",), "Ignored task."),
        (("fix!: remove old API",), "Ignored task."),
    ],
)
def test_derived_title_is_always_conventional(messages: tuple[str, ...], task: str) -> None:
    title = _subject_from_messages(_commit_messages(*messages), task)
    assert lc._parse_conventional_subject(title) is not None
    assert len(title) <= lc._SUBJECT_LIMIT


def test_control_characters_never_reach_a_title() -> None:
    # A subject is handed to git/gh as a subprocess argument, where an embedded NUL raises
    # rather than round-tripping, so the derived path must fall back to a printable subject.
    derived = _subject_from_messages(_commit_messages("fix: bad\x00null"), "Fallback description.")
    assert derived.isprintable()
    assert lc._parse_conventional_subject(derived) is not None


# One representative from each class str.isprintable() rejects, plus the line breaks that
# would smuggle an unvalidated second line into the PR title. Character safety is delegated
# to the Unicode database rather than an enumerated denylist, so a C0 control, a C1 control
# (the range a hand-written `\x00-\x1f\x7f` class missed), DEL, a format char, and every
# line/paragraph separator must all be rejected. Built from code points, never typed as
# literals: an editor silently folds a literal U+2028 into a space, hiding the case.
_NON_PRINTABLE = [
    0x00,  # NUL (C0)
    0x0A,  # LF
    0x0D,  # CR
    0x0B,  # VT
    0x1C,  # FS
    0x7F,  # DEL
    0x80,  # PAD (C1)
    0x9B,  # CSI (C1) — the class the enumerated denylist missed
    0x85,  # NEL
    0x2028,  # LINE SEPARATOR
    0x2029,  # PARAGRAPH SEPARATOR
    0x200B,  # ZERO WIDTH SPACE (format char)
]


@pytest.mark.parametrize("code_point", _NON_PRINTABLE)
def test_explicit_title_rejects_every_non_printable(code_point: int) -> None:
    with pytest.raises(ConfigError):
        lc._validate_explicit_title(f"fix: ok{chr(code_point)}smuggled")


def test_printable_unicode_title_is_accepted() -> None:
    # isprintable() safety must not over-reject: legitimate non-ASCII text is valid and
    # parses to a usable subject that preserves the description verbatim.
    title = "fix: café serves 日本語 fine"
    lc._validate_explicit_title(title)  # does not raise
    parsed = lc._parse_conventional_subject(title)
    assert parsed is not None
    assert parsed.description == "café serves 日本語 fine"


def test_incomplete_commit_is_non_releasing_and_preserves_trailers() -> None:
    message = _incomplete_commit_message(Step("main", "backend", "Repair capture."), "main")
    subject, _, body = message.partition("\n")
    assert subject == "chore: Repair capture. (incomplete step)"
    assert body.endswith("\nOrchestrator-Status: incomplete\nOrchestrator-PR-Base: main")


def test_incomplete_commit_names_the_work_not_the_task_heading() -> None:
    # Task prose always opens with `## What`, so a literal first line named the section.
    message = _incomplete_commit_message(
        Step("main", "backend", "## What\nRepair capture.\n\n## Why\nIt drops output.\n"), "main"
    )
    assert message.partition("\n")[0] == "chore: Repair capture. (incomplete step)"


def test_incomplete_marker_survives_task_prose_too_long_for_a_subject() -> None:
    # Preserving partial work must never fail, and a marker is still recognized by this
    # text when its trailer is missing, so the fallback keeps it rather than the prose.
    message = _incomplete_commit_message(Step("main", "backend", f"## What\n{'x' * 200}\n"), "main")
    subject = message.partition("\n")[0]
    assert subject == "chore: orchestrated change (incomplete step)"
    assert lc.is_provenance_commit(message)


@pytest.mark.parametrize(
    "task, expected",
    [
        ("## What\nRepair capture.\n", "Repair capture."),
        ("   \n### Heading\n\n  Repair capture.  \n", "Repair capture."),
        ("## What\n\n## Why\n", "orchestrated change"),
        ("", "orchestrated change"),
    ],
)
def test_task_description_skips_headings_and_blank_lines(task: str, expected: str) -> None:
    assert lc._task_description(task) == expected


def test_provenance_commits_never_contribute_to_a_derived_subject() -> None:
    """A marker and its attestation describe the run, so neither describes the change."""
    title = _subject_from_messages(
        _commit_messages(
            "feat: adopt the shared design system",
            "chore: Adopt the shared design system. (incomplete step)\n\n"
            f"{INCOMPLETE_TRAILER}\nOrchestrator-PR-Base: main",
            f"chore: attest verified recovery of preserved work\n\n{RECOVERY_TRAILER} abc123",
        ),
        "irrelevant task prose",
    )
    assert title == "feat: adopt the shared design system"


def test_a_branch_of_only_provenance_falls_back_to_the_task() -> None:
    title = _subject_from_messages(
        _commit_messages(
            f"chore: Repair capture. (incomplete step)\n\n{INCOMPLETE_TRAILER}",
            f"chore: attest verified recovery of preserved work\n\n{RECOVERY_TRAILER} abc123",
        ),
        "## What\nRecover preserved work.\n",
    )
    assert title == "chore: Recover preserved work."


def test_default_body_uses_structured_task_what_and_why() -> None:
    task = """
      ## What
      Add safer publication behavior.

      ## Why
      Prevent users from publishing incomplete work.
    """
    assert _default_body("reviewer", task, None) == (
        "## What\nAdd safer publication behavior.\n\n"
        "## Why\nPrevent users from publishing incomplete work.\n"
    )


def test_default_body_preserves_legacy_fallback() -> None:
    from orchestrator.dispatch import Report

    report = Report("p", 0, True, False, 3, [], {}, {}, "")
    body = _default_body("reviewer", "Do the thing.", report)
    assert "## What\nDo the thing." in body
    assert "reviewer" in body and "3 agent turn" in body


def test_task_pr_context_stops_at_acceptance_criteria_and_additional_info() -> None:
    task = """## What
Ship the user-visible behavior.

## Why
Remove a recurring user failure.

## Acceptance criteria
- The real request succeeds.

## Additional info
Internal rollout note that must not leak.
"""
    body = _default_body("engineer", task, None)
    assert body == (
        "## What\nShip the user-visible behavior.\n\n## Why\nRemove a recurring user failure.\n"
    )
    assert "Acceptance criteria" not in body
    assert "Internal rollout" not in body


def test_structured_workstream_body_uses_each_steps_what_and_why() -> None:
    steps = [
        Step("one", "engineer", "## What\nChange one.\n\n## Why\nHelp one."),
        Step("two", "writer", "## What\nChange two.\n\n## Why\nHelp two."),
    ]
    assert lc._workstream_body(steps, []) == (
        "## What\n- Change one.\n- Change two.\n\n## Why\n- Help one.\n- Help two.\n"
    )


def test_mixed_workstream_body_includes_structured_and_legacy_steps() -> None:
    steps = [
        Step("one", "engineer", "## What\nChange one.\n\n## Why\nHelp one."),
        Step("approval", None, "Approve the change.\nDo not include this line.", kind="human"),
        Step("two", "writer", "## What\nChange two.\n\n## Why\nHelp two."),
    ]
    assert lc._workstream_body(steps, []) == (
        "## What\n- Change one.\n- **approval** (`human`): Approve the change.\n"
        "- Change two.\n\n## Why\n- Help one.\n"
        "- **approval** (`human`): Approve the change.\n- Help two.\n"
    )


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
        "engineer",
        workspace=Workspace(tmp_path / "unused", resolver=lambda _spec: tmp_path / "missing"),
        stack_bases=[anchor],
    )
    assert result.outcome == "error" and expected in result.detail


def test_cleanup_suppression_does_not_mask_inflight_failure() -> None:
    from orchestrator.coordination import LockTimeout
    from orchestrator.gitops import GitError
    from orchestrator.journal import NullNodeJournal

    result = LifecycleResult("o/r", "task", "engineer", "main", "branch", "error")

    def fail_cleanup() -> None:
        raise LockTimeout("busy")

    with pytest.raises(GitError, match="publish failed"):
        try:
            raise GitError("publish failed")
        finally:
            lc._best_effort_cleanup(
                result,
                NullNodeJournal(),
                operation="remove-worktree",
                target="/tmp/worktree",
                cleanup=fail_cleanup,
            )

    assert result.deferred_cleanup == ["remove-worktree deferred for /tmp/worktree: busy"]


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
                    {"id": "a", "repo": "o/r", "persona": "engineer", "task": "A"},
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
                        "verify_via_ci": False,
                    },
                ],
            },
        )
    )
    assert plan.concurrency == 2
    assert [t.id for t in plan.tasks] == ["a", "b"]
    assert plan.tasks[1].merge_policy == "direct"
    assert plan.tasks[1].verify_via_ci is False
    assert plan.tasks[1].workflow == "local"
    assert plan.tasks[1].repo_type == "single-owner"
    assert plan.tasks[1].stack_bases[0].branch == "feature/parent"


@pytest.mark.parametrize("key", ["skip_verify", "no_identity_gate"])
def test_retired_gate_skipping_plan_keys_still_load_and_do_nothing(tmp_path, key: str) -> None:
    """Plans written before the merge path became authoritative keep loading.

    Neither key ever skipped merge-path verification, and neither can now, so
    both are accepted and carry no field into the lifecycle.
    """
    plan = load_repo_plan(
        _write(
            tmp_path,
            {
                "schema_version": 3,
                "tasks": [
                    {"id": "a", "repo": "o/r", "persona": "engineer", "task": "t", key: True},
                ],
            },
        )
    )
    assert [t.id for t in plan.tasks] == ["a"]
    assert not any(field.name == key for field in fields(RepoPlanNode))


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
                        "verify_via_ci": "yes",
                    }
                ]
            },
            "verify_via_ci",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "title": "Fix the release",
                    }
                ]
            },
            "Conventional Commit subject",
        ),
        (
            {
                "tasks": [
                    {
                        "id": "a",
                        "repo": "r",
                        "persona": "p",
                        "task": "t",
                        "title": 3,
                    }
                ]
            },
            "Conventional Commit subject",
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
                            {"id": "impl", "persona": "engineer", "task": "build"},
                            {
                                "id": "test",
                                "persona": "engineer",
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


def test_load_repo_plan_accepts_consistent_human_resume(tmp_path) -> None:
    node = {
        "id": "work",
        "repo": "o/r",
        "branch": "feature/work",
        "base_branch": "main",
        "steps": [
            {"id": "prepare", "persona": "engineer", "task": "Prepare"},
            {"id": "approve", "kind": "human", "task": "Approve", "deps": ["prepare"]},
        ],
        "resume": {
            "branch": "feature/work",
            "base_branch": "main",
            "pr_base": "main",
            "checkpoint": "abcdef1",
            "completed_steps": ["prepare", "approve"],
            "pr": "https://github.com/o/r/pull/1",
        },
    }

    plan = load_repo_plan(_write(tmp_path, {"tasks": [node]}))

    assert plan.tasks[0].resume is not None
    assert plan.tasks[0].resume.completed_steps == ("prepare", "approve")


@pytest.mark.parametrize(
    "update, match",
    [
        ({"branch": "feature/other"}, "conflicts with resume"),
        ({"base_branch": "develop"}, "conflicts with resume"),
        (
            {"resume": {"completed_steps": ["prepare", "prepare"]}},
            "must be unique",
        ),
        (
            {"resume": {"completed_steps": ["approve"]}},
            "missing completed dependencies: prepare",
        ),
        (
            {"resume": {"pr": "https://github.com/x/r/pull/1"}},
            "repository does not match 'repo'",
        ),
    ],
)
def test_load_repo_plan_rejects_inconsistent_human_resume(tmp_path, update, match) -> None:
    resume = {
        "branch": "feature/work",
        "base_branch": "main",
        "pr_base": "main",
        "checkpoint": "abcdef1",
        "completed_steps": ["prepare"],
        "pr": "https://github.com/o/r/pull/1",
    }
    node = {
        "id": "work",
        "repo": "o/r",
        "branch": "feature/work",
        "base_branch": "main",
        "steps": [
            {"id": "prepare", "persona": "engineer", "task": "Prepare"},
            {"id": "approve", "kind": "human", "task": "Approve", "deps": ["prepare"]},
        ],
        "resume": resume,
    }
    nested = update.get("resume")
    if nested is not None:
        node["resume"] = {**resume, **nested}
    else:
        node.update(update)

    with pytest.raises(PlanError, match=match):
        load_repo_plan(_write(tmp_path, {"tasks": [node]}))


def test_load_repo_plan_rejects_resume_without_human_workstream(tmp_path) -> None:
    node = {
        "id": "work",
        "repo": "o/r",
        "persona": "engineer",
        "task": "Work",
        "resume": {
            "branch": "feature/work",
            "base_branch": "main",
            "pr_base": "main",
            "checkpoint": "abcdef1",
            "completed_steps": [],
            "pr": None,
        },
    }

    with pytest.raises(PlanError, match="requires a steps workstream with a human step"):
        load_repo_plan(_write(tmp_path, {"tasks": [node]}))


def test_run_repo_task_requires_task_or_steps(tmp_path) -> None:
    with pytest.raises(ConfigError, match="needs either"):
        run_repo_task("o/r", workspace=Workspace(tmp_path / "ws"))


def test_workstream_branch_name_unique_and_task_identifiable() -> None:
    a = _workstream_branch_name([Step("impl", "engineer", "x")])
    assert a != _workstream_branch_name([Step("impl", "engineer", "x")])
    assert "/37808517-" in a
    b = _workstream_branch_name([Step("impl", "engineer", "x"), Step("test", "engineer", "y")])
    assert a != b and a.startswith("ai-orchestrator/engineer/")


def test_summary_shows_step_count() -> None:
    from orchestrator.lifecycle import StepResult

    result = _result(
        "merged",
        steps=[StepResult("a", "p", "done"), StepResult("b", "q", "done")],
    )
    assert "[2/2 steps]" in result.summary()


def test_run_repo_task_journals_the_workstream_and_labels_each_dispatch(
    tmp_path, bare_origin
) -> None:
    """The real lifecycle records its own transitions and labels its own dispatches.

    Driven end to end against a real origin: real git, the real step scheduler, the
    real local merge. Only the paid dispatch is a double, and it is there to capture
    the labels a subprocess would have carried — the one thing tying a recorded
    session back to the node that produced it.
    """
    from orchestrator import gitops
    from orchestrator.dispatch import Report
    from orchestrator.journal import NodeJournal, open_journal
    from orchestrator.runs import NodeId, RunId

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)
    labelled: dict[str, dict[str, str]] = {}
    wrapper_modes: list[bool] = []

    def fake_dispatch(persona, task, *, project_dir, labels=None, **kw):
        labelled[task] = dict(labels or {})
        wrapper_modes.append(kw["use_llmlint_wrapper"])
        Path(project_dir, f"{task}.txt").write_text(f"{task}\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    workspace = Workspace(
        tmp_path / "ws",
        resolver=lambda _url: publication,
        workflow="local",
        repo_type="single-owner",
    )
    journal = open_journal(tmp_path / "run-lc", RunId("run-lc"), 4)
    scope = NodeJournal(sink=journal, node=NodeId("api"), run_id=RunId("run-lc"), round=4)

    result = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=[
            Step("impl", "engineer", "impl"),
            Step("check", "engineer", "check", deps=["impl"]),
        ],
        recorded_gate=["true"],
        dispatch_fn=fake_dispatch,
        journal=scope,
    )

    assert result.outcome == "merged"
    assert wrapper_modes == [False, False]
    events = journal.events()
    located = [(e.kind, e.step) for e in events]
    assert ("branch-discovered", None) in located
    assert ("step-started", "impl") in located
    assert ("step-settled", "impl") in located
    assert ("step-started", "check") in located
    assert ("step-settled", "check") in located
    # The merge path is the only verifier, so its own gate runs are what the round
    # records: one per gated push, each with its verdict and preserved output.
    assert [e.detail["label"] for e in events if e.kind == "verification-finished"] == [
        f"branch push {result.branch}",
        f"publication push {result.branch} -> main",
    ]
    assert all(e.detail["ok"] is True for e in events if e.kind == "verification-finished")
    assert ("publication-finished", None) in located
    # The gate now runs behind `git push`, so the round records what dispatch saw
    # of the merge path: which hook will run, and the bar it stands for.
    (coverage,) = [e for e in events if e.kind == "merge-gate-coverage"]
    assert coverage.detail["pre_push_hook"] == str(gitops.hooks_dir(publication) / "pre-push")
    assert coverage.detail["expected_gate"] == ["true"]
    assert coverage.detail["required_checks_status"] == "not-applicable"
    # Every transition is attributed to the node the lifecycle was scoped to,
    # though nothing inside the lifecycle ever names it.
    assert {(e.node, e.run_id, e.round) for e in events} == {("api", "run-lc", 4)}

    # A dispatch carries the same coordinates its step's events do, so a recorded
    # session and a recorded transition agree.
    assert labelled["impl"] == {"run_id": "run-lc", "round": "4", "node": "api", "step": "impl"}
    assert labelled["check"] == {"run_id": "run-lc", "round": "4", "node": "api", "step": "check"}


def test_run_repo_task_journals_a_step_that_hit_the_turn_cap(tmp_path, bare_origin) -> None:
    """A step that does not complete settles as not-completed, saying so and why."""
    from orchestrator import gitops
    from orchestrator.dispatch import Report
    from orchestrator.journal import NodeJournal, open_journal
    from orchestrator.runs import NodeId, RunId

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)
    turn_budgets = []

    def fake_dispatch(persona, task, *, project_dir, labels=None, **kw):
        turn_budgets.append(kw["max_turns"])
        Path(project_dir, "partial.txt").write_text("partial\n", encoding="utf-8")
        return Report(
            persona,
            1,
            False,
            True,
            9,
            [],
            {},
            {},
            "worker-died: tracked worker exited or stopped heartbeating "
            "(watchdog pid 4242, agent exit status 143)",
            outcome="worker-died",
        )

    workspace = Workspace(
        tmp_path / "ws",
        resolver=lambda _url: publication,
        workflow="local",
        repo_type="single-owner",
    )
    journal = open_journal(tmp_path / "run-cap", RunId("run-cap"), 1)
    scope = NodeJournal(sink=journal, node=NodeId("api"), run_id=RunId("run-cap"), round=1)

    result = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=[Step("impl", "engineer", "impl")],
        recorded_gate=["true"],
        dispatch_fn=fake_dispatch,
        journal=scope,
    )

    assert result.outcome == "not-completed"
    # The dispatcher's account of the death is what a reader gets; carry it through
    # rather than replacing it with the outcome name.
    assert "worker-died" in result.detail
    assert "agent exit status 143" in result.detail
    settled = next(e for e in journal.events() if e.kind == "step-settled")
    assert settled.step == "impl"
    assert settled.detail["status"] == "not-completed"
    assert settled.detail["turns"] == 9
    # The turn cap preserved partial work on the branch; the journal is what says so.
    assert settled.detail["preserved"] is True
    assert turn_budgets == [lc.DEFAULT_LIFECYCLE_STEP_MAX_TURNS] * 3
    assert "pr-merged" not in [e.kind for e in journal.events()]


def test_run_repo_task_expects_no_diff_step_does_not_dispatch(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)
    workspace = Workspace(
        tmp_path / "ws",
        resolver=lambda _url: publication,
        workflow="local",
        repo_type="single-owner",
    )

    def unexpected_dispatch(*args, **kwargs):
        raise AssertionError("expects_no_diff must not dispatch")

    result = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=[Step("ready", task="certify unchanged", expects_no_diff=True)],
        recorded_gate=["true"],
        dispatch_fn=unexpected_dispatch,
    )

    assert result.outcome == "no-changes"
    assert result.steps[0].status == "done"
    assert result.steps[0].report is None


def test_run_repo_task_pauses_and_resumes_local_human_step(tmp_path, bare_origin) -> None:
    from orchestrator import gitops
    from orchestrator.dispatch import Report

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)
    calls: list[str] = []

    def fake_dispatch(persona, task, *, project_dir, **kw):
        calls.append(task)
        Path(project_dir, f"{task}.txt").write_text(f"{task}\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    workspace = Workspace(
        tmp_path / "ws",
        resolver=lambda _url: publication,
        workflow="local",
        repo_type="single-owner",
    )
    steps = [
        Step("prepare", "engineer", "prepare"),
        Step("approve", task="approve this", kind="human", deps=["prepare"]),
        Step("finish", "engineer", "finish", deps=["approve"]),
    ]

    paused = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=steps,
        recorded_gate=["true"],
        dispatch_fn=fake_dispatch,
    )

    assert paused.outcome == "waiting-human"
    assert paused.waiting_steps == ["approve"]
    assert paused.resume is not None
    assert paused.resume.pr is None
    assert [step.status for step in paused.steps] == ["done", "waiting", "blocked"]

    resume = Resume(
        branch=paused.resume.branch,
        base_branch=paused.resume.base_branch,
        pr_base=paused.resume.pr_base,
        checkpoint=paused.resume.checkpoint,
        completed_steps=(*paused.resume.completed_steps, "approve"),
        pr=paused.resume.pr,
    )
    finished = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=steps,
        recorded_gate=["true"],
        dispatch_fn=fake_dispatch,
        resume=resume,
    )

    assert finished.outcome == "merged"
    assert calls == ["prepare", "finish"]
    assert (publication / "prepare.txt").read_text(encoding="utf-8") == "prepare\n"
    assert (publication / "finish.txt").read_text(encoding="utf-8") == "finish\n"


def test_run_repo_task_remote_pause_creates_non_empty_draft(tmp_path, bare_origin) -> None:
    from orchestrator import gitops
    from orchestrator.dispatch import Report

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)
    created: list[dict[str, object]] = []

    class DraftGitHub:
        def default_branch(self, repo):
            return "main"

        def create_pr(self, repo, *, head, base, title, body, draft=False):
            created.append({"head": head, "base": base, "draft": draft})
            return PullRequest(5, "https://github.com/o/r/pull/5", repo, head, base)

        def mark_ready(self, pr):
            raise AssertionError("pause must not mark draft ready")

        def enable_auto_merge(self, pr, *, method):
            raise AssertionError("pause must not merge")

        def merge(self, pr, *, method):
            raise AssertionError("pause must not merge")

        def status(self, pr):
            raise AssertionError("pause should not poll draft status")

    def fake_dispatch(persona, task, *, project_dir, **kw):
        Path(project_dir, "prepare.txt").write_text("prepare\n", encoding="utf-8")
        return Report(persona, 0, True, False, 1, [], {}, {}, "")

    workspace = Workspace(
        tmp_path / "ws",
        resolver=lambda _url: publication,
        workflow="remote",
        repo_type="single-owner",
    )

    result = run_repo_task(
        str(origin),
        workspace=workspace,
        steps=[
            Step("prepare", "engineer", "prepare"),
            Step("approve", task="approve", kind="human", deps=["prepare"]),
        ],
        recorded_gate=["true"],
        dispatch_fn=fake_dispatch,
        github=DraftGitHub(),
    )

    assert result.outcome == "waiting-human"
    assert result.pr is not None
    assert result.resume is not None and result.resume.pr == "https://github.com/o/r/pull/5"
    assert created == [{"head": result.branch, "base": "main", "draft": True}]


def test_run_repo_task_remote_pause_does_not_create_empty_draft(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)

    class NoDraftGitHub:
        def create_pr(self, *args, **kwargs):
            raise AssertionError("empty pause must not create a draft")

    result = run_repo_task(
        str(origin),
        workspace=Workspace(
            tmp_path / "ws",
            resolver=lambda _url: publication,
            workflow="remote",
            repo_type="single-owner",
        ),
        steps=[Step("approve", task="approve", kind="human")],
        recorded_gate=["true"],
        github=NoDraftGitHub(),
    )

    assert result.outcome == "waiting-human"
    assert result.resume is not None and result.resume.pr is None


def test_run_repo_task_resume_fails_when_branch_is_missing(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)
    result = run_repo_task(
        str(origin),
        workspace=Workspace(
            tmp_path / "ws",
            resolver=lambda _url: publication,
            workflow="local",
            repo_type="single-owner",
        ),
        steps=[Step("approve", task="approve", kind="human")],
        recorded_gate=["true"],
        resume=Resume(
            "feature/missing", "main", "main", gitops.head_sha(publication), ("approve",)
        ),
    )

    assert result.outcome == "resume-failed"
    assert "no longer exists" in result.detail


def test_run_repo_task_resume_fails_when_checkpoint_is_missing(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)
    worktree = gitops.worktree_add(
        publication, tmp_path / "branch", "feature/resume", base="origin/main"
    )
    gitops.worktree_remove(publication, worktree)

    result = run_repo_task(
        str(origin),
        workspace=Workspace(
            tmp_path / "ws",
            resolver=lambda _url: publication,
            workflow="local",
            repo_type="single-owner",
        ),
        steps=[Step("approve", task="approve", kind="human")],
        recorded_gate=["true"],
        resume=Resume("feature/resume", "main", "main", "abcdef1", ("approve",)),
    )

    assert result.outcome == "resume-failed"
    assert "checkpoint" in result.detail


def test_run_repo_task_resume_fails_when_recorded_draft_is_closed(tmp_path, bare_origin) -> None:
    from orchestrator import gitops

    origin = bare_origin()
    publication = gitops.clone(origin, tmp_path / "publication")
    install_pre_push_hook(publication)
    worktree = gitops.worktree_add(
        publication, tmp_path / "branch", "feature/resume", base="origin/main"
    )
    checkpoint = gitops.head_sha(worktree)
    gitops.worktree_remove(publication, worktree)

    class ClosedDraftGitHub:
        def status(self, pr):
            return PRStatus(pr.number, "CLOSED", False, "CLEAN", ())

    result = run_repo_task(
        str(origin),
        workspace=Workspace(
            tmp_path / "ws",
            resolver=lambda _url: publication,
            workflow="remote",
            repo_type="single-owner",
        ),
        steps=[Step("approve", task="approve", kind="human")],
        recorded_gate=["true"],
        github=ClosedDraftGitHub(),
        resume=Resume(
            "feature/resume",
            "main",
            "main",
            checkpoint,
            ("approve",),
            "https://github.com/o/r/pull/9",
        ),
    )

    assert result.outcome == "resume-failed"
    assert "closed without merging" in result.detail


# --- scheduling ------------------------------------------------------------


def test_run_repo_plan_cascades(tmp_path) -> None:
    def runner(node: RepoPlanNode, **_: object) -> LifecycleResult:
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

    def runner(node: RepoPlanNode, **_: object) -> LifecycleResult:
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

    def runner(node: RepoPlanNode, **_: object) -> LifecycleResult:
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
        verify_via_ci=True,
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
        verify_via_ci=False,
        repo_type="single-owner",
    )
    out = runner(node)
    assert out.outcome == "merged"
    assert captured["repo"] == "o/r" and captured["persona"] == "reviewer"
    assert captured["merge_policy"] == "direct"  # node override wins
    assert captured["repo_type"] == "single-owner"
    assert captured["verify_via_ci"] is False
    assert captured["oneharness_mode"] == "bypass"


# --- error path ------------------------------------------------------------


def test_verify_via_ci_without_remote_path_fails_before_dispatch(tmp_path, bare_origin) -> None:
    dispatched: list[str] = []
    checkout = lc.gitops.clone(bare_origin(), tmp_path / "checkout")
    Registry().register(str(checkout), workflow="local", repo_type="single-owner")

    def dispatch_fn(*args: object, **kwargs: object):
        dispatched.append("called")
        raise AssertionError("dispatch must not run")

    result = run_repo_task(
        str(checkout),
        "t",
        "engineer",
        workspace=Workspace(tmp_path / "ws"),
        repo_type="single-owner",
        verify_via_ci=True,
        dispatch_fn=dispatch_fn,
    )

    assert result.outcome == "error"
    assert "remote GitHub/PR workflow" in result.detail
    assert dispatched == []


def test_run_repo_task_git_error_is_reported(tmp_path) -> None:
    result = run_repo_task(
        "acme/widget",
        "t",
        "engineer",
        workspace=Workspace(tmp_path / "ws"),
        url=str(tmp_path / "does-not-exist.git"),  # clone fails → GitError
        recorded_gate=["true"],
    )
    assert result.outcome == "error" and not result.ok and result.detail


# --- CLIs ------------------------------------------------------------------


def _one_node_lifecycle_plan(tmp_path: Path, **node: object) -> Path:
    """The plan file a single lifecycle dispatch is now expressed as."""
    plan = tmp_path / "one-node.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 6,
                "tasks": [
                    {"id": "solo", "repo": "acme/widget", "persona": "engineer", "task": "do it"}
                    | node
                ],
            }
        ),
        encoding="utf-8",
    )
    return plan


def test_one_node_plan_json_output_carries_the_lifecycle_outcome(
    monkeypatch, tmp_path, capsys
) -> None:
    captured: dict[str, object] = {}

    def fake_task(*args, **kwargs):
        captured.update(kwargs)
        return _result("merged", pr=PullRequest(1, "url", "o/r", "b", "main"))

    monkeypatch.setattr(lc, "run_repo_task", fake_task)
    rc = graph.main(
        [
            str(_one_node_lifecycle_plan(tmp_path)),
            "--no-record",
            "--verify-via-ci",
            "--format",
            "json",
        ]
    )
    assert rc == 0
    node = json.loads(capsys.readouterr().out)["results"]["solo"]
    assert node["outcome"] == "merged" and node["pr"] == "url"
    assert captured["verify_via_ci"] is True


def test_one_node_plan_rejects_nonpositive_publication_attempts(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        graph.main([str(_one_node_lifecycle_plan(tmp_path)), "--publication-attempts", "0"])
    assert exc.value.code == 2
    assert "must be at least 1" in capsys.readouterr().err


def test_one_node_plan_rejects_invalid_literal_branch_before_resolution(tmp_path, capsys) -> None:
    """The plan path refuses this before the round is claimed, not per node.

    The removed `just repo-task --branch` reached the workstream and came back an
    `error` outcome. A plan file is validated up front, so the same bad value is a
    correctable usage error naming the node it came from.
    """
    rc = graph.main([str(_one_node_lifecycle_plan(tmp_path, branch="bad..branch")), "--no-record"])
    assert rc == 2
    stderr = capsys.readouterr().err
    assert "task 'solo'" in stderr and "valid non-empty Git branch" in stderr


def test_one_node_plan_rejects_nonconventional_explicit_title(tmp_path, capsys) -> None:
    rc = graph.main(
        [str(_one_node_lifecycle_plan(tmp_path, title="Fix the release")), "--no-record"]
    )
    assert rc == 2
    stderr = capsys.readouterr().err
    assert "task 'solo'" in stderr and "Conventional Commit subject" in stderr


def test_one_node_plan_human_nonzero_on_failure(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(lc, "run_repo_task", lambda *a, **k: _result("gate-failed"))
    rc = graph.main([str(_one_node_lifecycle_plan(tmp_path)), "--no-record"])
    assert rc == 1
    assert "gate-failed" in capsys.readouterr().out


def test_one_node_plan_writes_output_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(lc, "run_repo_task", lambda *a, **k: _result("merged"))
    out = tmp_path / "r.json"
    rc = graph.main(
        [
            str(_one_node_lifecycle_plan(tmp_path, persona="reviewer")),
            "--no-record",
            "--format",
            "json",
            "-o",
            str(out),
        ]
    )
    assert rc == 0 and '"outcome"' in out.read_text(encoding="utf-8")


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


def test_recovery_result_defaults_publication_base() -> None:
    result = RecoveryResult(
        "local/repo", "feature", "main", "local", "single-owner", "direct", "merged", "done"
    )

    assert result.pr_base == "main" and result.ok
