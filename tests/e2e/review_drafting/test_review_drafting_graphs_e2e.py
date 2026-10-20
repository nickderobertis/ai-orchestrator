"""The two drafting graphs a review round reaches, driven on the real `oneagentgraph`.

A review round reaches two drafters this repository owns. `graphs/review-reply.yaml`
answers each reviewer comment a rework answered or a `decline` declined, and
`graphs/pr-author.yaml` drafts a reworked change request's body and says whether its
title still describes the change. On a real run both are reached from a change request on
a real host — `onepipeline review`, a `decline`, a rework's republication — which this
suite performs none of, because that would post on a real repository. What it drives is
everything below that boundary: the real `oneagentgraph`, the real graph documents, the
real `oneharness.*.toml` and the real schemas, with only the paid provider scripted.

Each composed task here is this journey's own, written to the sections the engine names
— `tests/test_engine_contracts.py` holds every heading and sentence the prompts read to
the installed engine — rather than a copy of the engine's preamble, which would be a
second copy of somebody else's contract that nothing reconciles. What a turn was asked is
read off `tests/e2e/fake_codex.py`'s prompt record, because a single-sided member's turn
is an in-process oneharness call that no other seam observes; what it answered is read
off the member report the engine reads its structured result from.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TypedDict, cast

import short_state
from harness_indirections import established_indirections
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT


def helper(name: str) -> Path:
    """A stand-in this project's own dependencies own, refused by name when absent.

    Resolved here, where `project_fixtures.helper` resolves it, rather than imported from
    it, whose project's inputs are the planning and publication modules no drafting
    journey reads; spelled `helper` so the cache-scope scan still keys this project on
    each stand-in it names. A stand-in for a paid provider that does not exist guards
    nothing — oneharness falls through to a real identity — so its absence fails here
    before any turn is spent.
    """
    found = REPO_ROOT / "tests" / "e2e" / name
    assert found.exists(), f"the paid provider's stand-in {name!r} is not at {found}"
    return found


#: The paid provider's stand-in, named to oneharness as codex's: the one identity each
#: drafting config here names first.
FAKE_CODEX = helper("fake_codex.py")

#: Ahead of everything on `PATH`, a `claude` that refuses the turn, covering the
#: identities the codex seam cannot reach; see `tests/e2e/no_paid_provider.py`.
PAID_PROVIDER_GUARD = helper("no-paid-provider")

REVIEW_REPLY_GRAPH = "graphs/review-reply.yaml"
PR_AUTHOR_GRAPH = "graphs/pr-author.yaml"
REVIEW_REPLY_SCHEMA = REPO_ROOT / "config" / "review-reply.schema.json"
REVIEW_REPLY_CONFIG = REPO_ROOT / "oneharness.review-reply.toml"

#: Who the indirection helpers attribute their diagnostics to.
INDIRECTION_CALLER = "tests/e2e/review_drafting/test_review_drafting_graphs_e2e.py"

#: The no-transcript sentence, which the engine writes under `## Worker transcript` where
#: this host's journal holds no transcript — always for a decline. Spelled here, not
#: read from the graph, so a graph that stopped naming it fails rather than agreeing.
NO_TRANSCRIPT = "No worker transcript is available on this host; draft from the sections above."

CHANGE_URL = "https://github.com/example/service/pull/4"
THREAD_COMMENT = "PRRC_kwABzgAAAlk"
THREAD_COMMENT_BODY = "A cache here hides a stale read; why not retry the read instead?"
CONVERSATION_COMMENT = "IC_kwABzgAAAfU"
CONVERSATION_COMMENT_BODY = "Could this also add a metrics counter for cache hits?"
COMMIT_URL = "https://github.com/example/service/commit/1f2e3d4c5b6a79881726354453627180918a7b6c"
COMMIT_LINE = (
    f"- 1f2e3d4c5b6a79881726354453627180918a7b6c fix(reader): retry a failed read {COMMIT_URL}"
)
REWORK_REASON = (
    "The reviewer asked for a retry instead of a cache.\n\n"
    "Remove the read-through cache and retry a failed read once."
)
DECLINE_REASON = "Out of scope for this change: metrics are tracked in #42."
TRANSCRIPT_PATH = "/runs/review-run/transcripts/reader-rework-1.jsonl"


def _comment(comment_id: str, url: str, location: str, body: str) -> str:
    return (
        f"### {comment_id}\n\n- URL: {url}\n- Author: reviewer\n- Location: {location}\n\n{body}\n"
    )


def _reply_task(*, comments: list[str], commits: list[str], reason: str, transcript: str) -> str:
    """A reply drafting's composed task, in the five sections the engine writes."""
    return (
        "Draft one terse reply to each review comment below.\n\n"
        f"## Change request\n\n{CHANGE_URL}\n\n"
        "## Comments\n\n" + "\n".join(comments) + "\n"
        "## Commits\n\n" + "".join(f"{line}\n" for line in commits) + "\n"
        f"## Reason\n\n{reason}\n\n"
        f"## Worker transcript\n\n{transcript}\n"
    )


THREAD = _comment(
    THREAD_COMMENT, f"{CHANGE_URL}#discussion_r601", "src/reader.rs:42", THREAD_COMMENT_BODY
)
CONVERSATION = _comment(
    CONVERSATION_COMMENT,
    f"{CHANGE_URL}#issuecomment-501",
    "conversation comment",
    CONVERSATION_COMMENT_BODY,
)

#: The three drafting tasks the engine composes for this graph: a rework whose worker's
#: transcript this host holds, a decline, and a rework drafted on a host that did not
#: run its worker.
ANSWERED_TASK = _reply_task(
    comments=[THREAD], commits=[COMMIT_LINE], reason=REWORK_REASON, transcript=TRANSCRIPT_PATH
)
DECLINE_TASK = _reply_task(
    comments=[CONVERSATION], commits=[], reason=DECLINE_REASON, transcript=NO_TRANSCRIPT
)
COLD_REWORK_TASK = _reply_task(
    comments=[THREAD], commits=[COMMIT_LINE], reason=REWORK_REASON, transcript=NO_TRANSCRIPT
)

ANSWERED = {
    "replies": [
        {
            "comment": THREAD_COMMENT,
            "disposition": "answered",
            "body": f"Dropped the cache; a failed read is retried once: {COMMIT_URL}",
        }
    ]
}
DECLINED = {
    "replies": [
        {
            "comment": CONVERSATION_COMMENT,
            "disposition": "declined",
            "body": "Left as it is: metrics are tracked in #42.",
        }
    ]
}

#: A body drafting's composed task on a rework, carrying the engine's `## Rework`,
#: `## Current title` and `## Net diff` sections.
REWORK_BODY_TASK = (
    "Draft the body for `reader`, which reads a value through a cache.\n\n"
    "## Rework\n\nThe reviewer asked for a retry instead of a cache.\n\n"
    "## Current title\n\nfeat(reader): cache every read\n\n"
    "## Net diff\n\n```diff\n--- a/src/reader.rs\n+++ b/src/reader.rs\n"
    "-    cache.get_or_read(key)\n+    retry_once(|| read(key))\n```\n"
)
REWORK_BODY = "## What\nA failed read is retried once.\n\n## Why\nA cache hid stale reads.\n"

#: A body drafting's composed task carrying the Stack block the engine writes.
STACK_BLOCK = (
    "<!-- onepipeline:stack -->\n## Stack\n\nDrafted against `nick/plan/reader`, stacked on "
    "https://github.com/example/service/pull/3.\n<!-- /onepipeline:stack -->"
)
STACKED_BODY_TASK = (
    "Draft the body for `reader-metrics`, which counts reads.\n\n"
    "## Change request\n\nhttps://github.com/example/service/pull/5\n\n"
    f"{STACK_BLOCK}\n"
)


class DraftingResult(TypedDict):
    """The fields of one member's oneharness result this journey reads."""

    structured: dict[str, object] | None
    schema_valid: bool | None
    schema_attempts: int | None


class DraftingReport(TypedDict):
    """The member report a drafting run writes, narrowed to what is read here."""

    results: list[DraftingResult]


class Drafted(TypedDict):
    """What one drafting run left: its report, and every prompt the provider received."""

    report: DraftingReport
    prompts: list[str]


def _environment(tmp_path: Path, answers: list[str]) -> dict[str, str]:
    """This suite's environment with only the paid provider substituted.

    Every override a dispatch, a launcher or another journey left in the environment is
    dropped first — oneharness's own variables and history among them — so the host's
    history store and routing cannot decide this journey's verdicts.
    """
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ONEHARNESS_", "FAKE_CODEX_", "ONEPIPELINE_", "ONEAGENTGRAPH_"))
    }
    # llmlint: ignore-block[e2e_not_mocked] Only the paid provider process is substituted;
    # the graph, the member's task, oneharness and the schema review are all real.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["FAKE_CODEX_ANSWERS"] = json.dumps(answers)
    # The stand-in counts its launches here, which is how a re-prompt is given the next
    # scripted answer rather than the first one again.
    environment["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "launches")
    environment["FAKE_CODEX_PROMPT_LOG"] = str(tmp_path / "prompts.jsonl")
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    # llmlint: ignore-end[e2e_not_mocked]
    environment.update(established_indirections(INDIRECTION_CALLER))
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    return environment


def _drafted(tmp_path: Path, graph: str, task: str, answers: list[object]) -> Drafted:
    """Run one drafting graph on a scripted provider and hand back what it left."""
    # The member's turn runs in this directory, so it has to exist before the turn does.
    tmp_path.mkdir(parents=True, exist_ok=True)
    environment = _environment(
        tmp_path, [answer if isinstance(answer, str) else json.dumps(answer) for answer in answers]
    )
    ran = subprocess.run(  # noqa: S603 - the pinned graph CLI
        ["oneagentgraph", "run", graph, "--task", task, "--dir", str(tmp_path)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    settled = [
        json.loads(line)
        for line in ran.stdout.splitlines()
        if line.startswith("{") and json.loads(line)["kind"] == "member-settled"
    ]
    assert settled, f"the drafter never settled:\n{ran.stdout}\n{ran.stderr}"
    # oneharness owns this record's schema; DraftingReport names the fields read here.
    report = cast(
        DraftingReport,
        json.loads(Path(settled[0]["payload"]["report_path"]).read_text(encoding="utf-8")),
    )
    log = Path(environment["FAKE_CODEX_PROMPT_LOG"])
    assert log.is_file(), f"no turn of {graph} reached the provider stand-in"
    prompts = [
        str(json.loads(line)["prompt"]) for line in log.read_text(encoding="utf-8").splitlines()
    ]
    return Drafted(report=report, prompts=prompts)


def _flat(text: str) -> str:
    """`text` with its whitespace collapsed, so a wrapped instruction reads as one line."""
    return " ".join(text.split())


def _assert_prompted(prompt: str, task: str, instructions: tuple[str, ...]) -> None:
    """The turn saw the whole composed task, then each instruction after it."""
    assert task in prompt, (
        "the drafting turn was not prompted with the whole composed task; the member's own "
        f"task must interpolate `{{task}}` back in:\n{prompt}"
    )
    flat = _flat(prompt)
    after = flat.index(_flat(task)) + len(_flat(task))
    for instruction in instructions:
        assert _flat(instruction) in flat[after:], (
            f"the drafting turn was not told {instruction!r} after the sections it reads:\n{prompt}"
        )


def _answered(drafted: Drafted) -> DraftingResult:
    answered = drafted["report"]["results"][-1]
    assert answered["schema_valid"] is True, answered
    return answered


def test_the_reply_graph_is_one_the_adopted_reader_accepts_with_the_agreed_schema() -> None:
    """`graphs/review-reply.yaml` validates, and its schema is the agreed closed contract."""
    validated = subprocess.run(  # noqa: S603 - the pinned graph CLI
        ["oneagentgraph", "validate", REVIEW_REPLY_GRAPH],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
    schema = json.loads(REVIEW_REPLY_SCHEMA.read_text(encoding="utf-8"))
    reply = schema["properties"]["replies"]["items"]
    assert (schema["required"], schema["additionalProperties"]) == (["replies"], False)
    assert (reply["required"], reply["additionalProperties"]) == (
        ["comment", "disposition", "body"],
        False,
    )
    assert reply["properties"]["disposition"]["enum"] == ["answered", "declined"]
    assert (
        reply["properties"]["body"]["minLength"],
        reply["properties"]["body"]["maxLength"],
    ) == (1, 500), "the engine posts a reply body of at most 500 characters and never an empty one"


def test_an_answered_rework_is_prompted_to_link_the_commit_that_answers_it(tmp_path: Path) -> None:
    """A rework's drafting sees every section and is told to link a commit from `## Commits`."""
    drafted = _drafted(tmp_path, REVIEW_REPLY_GRAPH, ANSWERED_TASK, [ANSWERED])

    prompt = drafted["prompts"][0]
    for content in (CHANGE_URL, THREAD_COMMENT_BODY, COMMIT_LINE, REWORK_REASON, TRANSCRIPT_PATH):
        assert content in prompt, f"the drafting turn was not shown {content!r}:\n{prompt}"
    _assert_prompted(
        prompt,
        ANSWERED_TASK,
        (
            "links the specific commit or commits that answer that comment, by the URLs "
            "`## Commits` gives",
            "Where `## Worker transcript` names a path, you may read it",
            "Never say or imply that a thread is resolved",
        ),
    )
    assert _answered(drafted)["structured"] == ANSWERED


def test_a_decline_is_prompted_to_say_why_and_link_nothing(tmp_path: Path) -> None:
    """A decline's drafting sees its reason and the no-transcript sentence, and links nothing."""
    drafted = _drafted(tmp_path, REVIEW_REPLY_GRAPH, DECLINE_TASK, [DECLINED])

    prompt = drafted["prompts"][0]
    for content in (CHANGE_URL, CONVERSATION_COMMENT_BODY, DECLINE_REASON, NO_TRANSCRIPT):
        assert content in prompt, f"the drafting turn was not shown {content!r}:\n{prompt}"
    _assert_prompted(
        prompt,
        DECLINE_TASK,
        (
            "A `declined` reply says in one sentence why the comment is left as it is, from "
            "the reason under `## Reason`, and links nothing.",
        ),
    )
    assert _answered(drafted)["structured"] == DECLINED


def test_a_rework_with_no_transcript_is_drafted_from_the_other_sections_alone(
    tmp_path: Path,
) -> None:
    """The no-transcript sentence tells the drafter to claim nothing the sections do not say."""
    drafted = _drafted(tmp_path, REVIEW_REPLY_GRAPH, COLD_REWORK_TASK, [ANSWERED])

    prompt = drafted["prompts"][0]
    for content in (CHANGE_URL, THREAD_COMMENT_BODY, COMMIT_LINE, REWORK_REASON, NO_TRANSCRIPT):
        assert content in prompt, f"the drafting turn was not shown {content!r}:\n{prompt}"
    _assert_prompted(
        prompt,
        COLD_REWORK_TASK,
        (
            f"Where it reads `{NO_TRANSCRIPT}` — always so for a decline — draft from the other "
            "sections alone, and state nothing about the work that they do not.",
        ),
    )
    assert _answered(drafted)["structured"] == ANSWERED


def test_a_reply_the_schema_refuses_is_re_prompted_rather_than_read(tmp_path: Path) -> None:
    """A body over 500 characters and an unknown disposition are each refused and asked again."""
    for name, refused in (
        (
            "overlong",
            {"replies": [{**ANSWERED["replies"][0], "body": f"{'x' * 470} {COMMIT_URL}"}]},
        ),
        (
            "unknown-disposition",
            {"replies": [{**ANSWERED["replies"][0], "disposition": "resolved"}]},
        ),
    ):
        drafted = _drafted(tmp_path / name, REVIEW_REPLY_GRAPH, ANSWERED_TASK, [refused, ANSWERED])

        answered = _answered(drafted)
        assert answered["structured"] == ANSWERED, f"{name}: {answered}"
        assert answered["schema_attempts"] == 2, (
            f"the {name} reply settled in {answered['schema_attempts']} attempt(s), so "
            f"{REVIEW_REPLY_SCHEMA.name} accepted an answer the engine could not post"
        )
        assert len(drafted["prompts"]) == 2, drafted["prompts"]


def test_a_reworked_body_is_prompted_for_a_title_check_and_answers_either_verdict(
    tmp_path: Path,
) -> None:
    """A rework's body drafting reads the net diff and answers `keeps` or `replaced`."""
    for verdict in (
        {"verdict": "keeps"},
        {"verdict": "replaced", "title": "fix(reader): retry a failed read instead of caching it"},
    ):
        answer = {"body": REWORK_BODY, "title_check": verdict}
        drafted = _drafted(
            tmp_path / str(verdict["verdict"]), PR_AUTHOR_GRAPH, REWORK_BODY_TASK, [answer]
        )

        prompt = drafted["prompts"][0]
        for section in ("## Rework", "## Net diff", "retry_once(|| read(key))"):
            assert section in prompt, f"the drafting turn was not shown {section!r}:\n{prompt}"
        _assert_prompted(
            prompt,
            REWORK_BODY_TASK,
            (
                "Read the patch under `## Net diff`",
                "answer `title_check` beside `body`",
                "with a Conventional Commit subject that does",
            ),
        )
        assert _answered(drafted)["structured"] == answer


def test_a_title_check_with_an_unknown_verdict_is_re_prompted(tmp_path: Path) -> None:
    """The body schema refuses a verdict the engine does not read, and accepts one without it."""
    answer = {"body": REWORK_BODY, "title_check": {"verdict": "keeps"}}
    drafted = _drafted(
        tmp_path,
        PR_AUTHOR_GRAPH,
        REWORK_BODY_TASK,
        [{"body": REWORK_BODY, "title_check": {"verdict": "maybe"}}, answer],
    )

    answered = _answered(drafted)
    assert answered["structured"] == answer
    assert answered["schema_attempts"] == 2, (
        f"a `maybe` verdict settled in {answered['schema_attempts']} attempt(s), so the body "
        "schema accepted a title check the engine refuses"
    )


def test_a_stacked_body_drafting_reads_the_engines_stack_block_and_writes_none(
    tmp_path: Path,
) -> None:
    """The Stack block reaches the drafter, which is told it is the engine's to write."""
    body = "## What\nCounts reads.\n\n## Why\nOperators had no read rate.\n"
    drafted = _drafted(tmp_path, PR_AUTHOR_GRAPH, STACKED_BODY_TASK, [{"body": body}])

    prompt = drafted["prompts"][0]
    assert STACK_BLOCK in prompt, f"the drafting turn was not shown the Stack block:\n{prompt}"
    _assert_prompted(
        prompt,
        STACKED_BODY_TASK,
        ("that block is the engine's", "write no Stack section of your own"),
    )
    assert _answered(drafted)["structured"] == {"body": body}
