"""Real streaming journeys for the agent side of a dispatch.

The planner supervises through what the harness reports, and until this the harness
reported nothing until a turn *ended* — on this host, 600-2000 seconds later. These
journeys drive the real `scripts/oneharness-agent.sh` against the real `oneharness`
CLI, over this repository's own `oneharness.toml` and its `run_mode = "fallback"`
chain. Only the paid model is replaced, at the repository's designated external
seam: oneharness's own shipped `--mock-harness` responder, driven through
`tests/e2e/mock_oneharness.py` exactly as the dispatch journeys drive it.

Nothing about the stream itself is faked. The events these assert on are produced by
the real oneharness normalizer from a real harness transcript, delivered over the
real NDJSON protocol, through the real filter, into the real status directory the
planner views read.
"""

# llmlint: ignore-file[e2e_not_mocked] the repository requires faking the paid agent
# harness and does it here at its designated seam (oneharness's own shipped mock
# responder); the wrapper, the oneharness CLI, the fallback chain, the stream
# protocol, the filter, the status directory and the `orchestrator-status` CLI are
# all real.

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest
from mock_oneharness import main as _mock_oneharness_main
from waits import deadline, timeout

from orchestrator import REPO_ROOT
from orchestrator.activity import MAX_SUMMARY_BYTES, STALE_AFTER_SECONDS
from orchestrator.journal import open_journal
from orchestrator.runs import NodeId, RunId
from orchestrator.scratch import AGENT_STATUS_DIR_NAME, owned_scratch_directory

MOCK_ONEHARNESS = Path(_mock_oneharness_main.__globals__["__file__"]).resolve()
#: How long the mocked harness waits between the lines of its transcript. Long
#: enough that a poll can observe the turn while it is still running — which is the
#: whole claim — and short enough that the journey stays a few seconds.
STREAM_STEP_MS = 1200
#: One Claude Code `stream-json` transcript: three tool calls, then the answer.
#: oneharness normalizes these into its own `tool_call` events, so the shapes the
#: filter and the reader see are the real ones.
TRANSCRIPT = (
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "Bash",
                    "input": {"command": "just check"},
                }
            ]
        },
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-2",
                    "name": "Read",
                    "input": {"file_path": "orchestrator/status.py"},
                }
            ]
        },
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-3",
                    "name": "Bash",
                    "input": {"command": "just gate"},
                }
            ]
        },
    },
    {"type": "result", "result": "the streamed turn finished"},
)


@pytest.fixture
def dispatch_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ExitStack]:
    """Create dispatch scratch the way a dispatch does, under this test's own root.

    `owned_scratch_directory` is the production path — `run_onejudge` enters it for
    every dispatch, and it holds the directory's owner lock for that dispatch's whole
    scope, which is the only evidence `orchestrator.activity` accepts. Pointing
    `tempfile` at this test's root is what lets a journey both create it that way and
    tell the `orchestrator-status` subprocess where to look.
    """
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "scratch"))
    (tmp_path / "scratch").mkdir()
    with ExitStack() as stack:
        yield stack


def _status_dir(stack: ExitStack) -> Path:
    """One live dispatch's agent status directory, exactly as `run_onejudge` makes it."""
    status_dir = stack.enter_context(owned_scratch_directory()) / AGENT_STATUS_DIR_NAME
    status_dir.mkdir()
    return status_dir


def _rejecting_codex(path: Path) -> Path:
    """A real binary for the `codex` candidate that refuses before doing any work.

    This is the 429/auth shape the fallback chain exists for: a rejection with no
    work behind it, which oneharness classifies as `auth` and falls through.
    """
    path.write_text(
        "#!/usr/bin/env bash\necho '401 Unauthorized: no credentials' >&2\nexit 1\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _wrapper_environment(
    tmp_path: Path, oneharness_bin: str, status_dir: Path, **overrides: str
) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    oneharness = bin_dir / "oneharness"
    if not oneharness.exists():
        oneharness.symlink_to(MOCK_ONEHARNESS)
    return {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(REPO_ROOT / "tests" / "e2e"),
        "REAL_ONEHARNESS_BIN": oneharness_bin,
        "ORCHESTRATOR_AGENT_STATUS_DIR": str(status_dir),
        # `--mock-harness` names the selected harness whose provider process the
        # shipped responder replaces; the chain is narrowed to plain ids so the mock
        # can name one, which is how the dispatch journeys drive it too.
        "MOCK_HARNESSES": "claude-code",
        "ONEHARNESS_HARNESSES": "codex,claude-code",
        "MOCK_STDOUT": "\n".join(json.dumps(line) for line in TRANSCRIPT),
        "MOCK_STREAM_DELAY_MS": str(STREAM_STEP_MS),
        **overrides,
    }


def _read_activity(path: Path) -> dict[str, object] | None:
    """Read one published summary, tolerating the instant before it is first written."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def test_the_agent_side_streams_its_turn_over_the_real_fallback_chain(
    tmp_path: Path, oneharness_bin: str, dispatch_scratch: ExitStack
) -> None:
    """A live turn is visible while it runs, and the chain that survives a 429 is kept.

    Four things have to hold at once, and each is one of the reasons the previous
    `--events` arrangement could not be kept:

    * the chain still falls through a rejection that did no work;
    * the candidate that falls through publishes nothing a consumer could act on,
      while its transcript is still in the report;
    * the selected candidate's events arrive *before* its turn ends;
    * onejudge's contract is untouched — stdout is still exactly one report.
    """
    status_dir = _status_dir(dispatch_scratch)
    activity = status_dir / "agent.activity"
    environment = _wrapper_environment(
        tmp_path,
        oneharness_bin,
        status_dir,
        ONEHARNESS_BIN_CODEX=str(_rejecting_codex(tmp_path / "rejecting-codex")),
        ONEHARNESS_HISTORY_LABELS="run_id=stream-run,round=1,node=ship,persona=engineer",
    )

    with subprocess.Popen(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "oneharness-agent.sh"),
            "run",
            "--compact",
            "--events",
            "--prompt",
            "stream this turn",
        ],
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    ) as process:
        try:
            observed: list[dict[str, object]] = []
            limit = deadline(60)
            # Observe the turn while it is still running. The process being alive here
            # is the assertion: a summary read after it exits would prove only that
            # the transcript survived, which `--events` already guaranteed.
            while time.monotonic() < limit and process.poll() is None:
                summary = _read_activity(activity)
                if summary is not None and summary not in observed:
                    observed.append(summary)
                    if len(observed) >= 2:
                        break
                time.sleep(0.05)
            assert len(observed) >= 2, (
                "no activity was published before the turn ended; "
                f"observed {observed}, exited {process.poll()}"
            )
            mid_turn = observed[0]
            assert mid_turn["kind"] == "tool_call"
            assert mid_turn["name"] == "Bash"
            assert mid_turn["detail"] == "just check"
            # The locator the dispatch was given, carried with the summary, because a
            # reader that finds this file under an anonymous scratch directory has no
            # other way to learn which node it describes.
            assert mid_turn["run_id"] == "stream-run"
            assert (mid_turn["round"], mid_turn["node"]) == ("1", "ship")
            # A later event replaced the earlier one rather than accumulating: the
            # summary says what the node is doing now.
            assert observed[1]["events"] == 2
            assert observed[1]["name"] == "Read"

            captured, errors = process.communicate(timeout=timeout(60))
        finally:
            process.kill()

    assert process.returncode == 0, errors
    # onejudge parses this process's stdout as exactly one JSON document, and it must
    # not be able to tell a streamed turn from a buffered one.
    assert len(captured.strip().splitlines()) == 1, captured
    report = json.loads(captured)
    assert report["fallback"] == {
        "ran": "claude-code",
        "fell_through": [{"harness": "codex", "reason": "auth"}],
    }
    results = {result["harness_id"]: result for result in report["results"]}
    # The rejection did no work, so it published nothing — and its whole transcript
    # is still in the report rather than discarded.
    assert results["codex"]["failure_kind"] == "auth"
    assert not results["codex"]["events"]
    assert "401 Unauthorized" in results["codex"]["stderr"]
    # `--stream` implies `--events`' format selection, so the transcript the previous
    # arrangement guaranteed is still in the end-of-turn report.
    assert [event["name"] for event in results["claude-code"]["events"]] == [
        "Bash",
        "Read",
        "Bash",
    ]
    # The raw record is what `tee` kept before: every line the child wrote, and only
    # the selected candidate's — three events plus the terminal result.
    streamed = (status_dir / "agent.stdout").read_text(encoding="utf-8").strip().splitlines()
    kinds = [json.loads(line)["type"] for line in streamed]
    assert kinds == ["event", "event", "event", "result"]


def test_a_dispatch_that_cannot_stream_still_runs_and_records_its_transcript(
    tmp_path: Path, oneharness_bin: str, dispatch_scratch: ExitStack
) -> None:
    """`--events` is the degrade path, and reaching it is never a dispatch failure.

    `parallel` really is several concurrent results whose event streams would
    interleave, so oneharness refuses to stream it — the "mode outside the supported
    set" case, indistinguishable at this boundary from an older CLI that does not
    know the flag. The turn must run anyway, and must still carry the tool transcript
    the planner views render.
    """
    status_dir = _status_dir(dispatch_scratch)
    environment = _wrapper_environment(
        tmp_path,
        oneharness_bin,
        status_dir,
        MOCK_HARNESSES="codex,claude-code",
        ONEHARNESS_RUN_MODE="parallel",
        MOCK_STREAM_DELAY_MS="0",
    )

    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "oneharness-agent.sh"),
            "run",
            "--compact",
            "--events",
            "--prompt",
            "buffer this turn",
        ],
        text=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=environment,
        timeout=timeout(60),
    )

    assert completed.returncode == 0, completed.stderr
    assert len(completed.stdout.strip().splitlines()) == 1, completed.stdout
    report = json.loads(completed.stdout)
    # Both ran, and both reported their normalized transcript — which is exactly what
    # `--events` was adopted for and what degrading must not cost.
    assert {result["harness_id"] for result in report["results"]} == {"codex", "claude-code"}
    for result in report["results"]:
        assert [event["name"] for event in result["events"]] == ["Bash", "Read", "Bash"]
    # Nothing was published, because nothing was streamed. The view falls back to the
    # journal join, which is the picture it had before streaming existed.
    assert not (status_dir / "agent.activity").exists()


def test_status_reports_what_a_node_is_doing_while_its_turn_is_still_running(
    tmp_path: Path, oneharness_bin: str, dispatch_scratch: ExitStack
) -> None:
    """The planner-visible view, mid-turn, against a real streaming dispatch.

    `just status <run-id>` once printed "No dispatched tasks recorded" for a node
    that had been working for thirty minutes. The journal join fixed the first half
    of that — *dispatched, no completed turn* is no longer *no dispatch*. This is the
    second half: the view now says what the node is doing, read from the turn itself
    rather than inferred from elapsed time.
    """
    scratch = tmp_path / "scratch"
    runs_dir = tmp_path / "runs"
    status_dir = _status_dir(dispatch_scratch)
    # The journal is this view's production input — the executor writes it through
    # this same append-only API and `just status` reads it — so writing the one
    # recorded transition is the input, not a shortcut past the entry point. Driving a
    # whole tracked round to reach an in-flight node would test the executor instead,
    # and `tests/e2e/test_status_e2e.py` reaches this view through the same seam.
    # llmlint: ignore[tests_mirror_real_usage] the journal is this view's own input
    journal = open_journal(runs_dir / "live-run", RunId("live-run"), 1)
    # llmlint: ignore[tests_mirror_real_usage] the recorded transition, via its own API
    journal.append("node-started", node=NodeId("ship"), detail={"persona": "engineer"})
    # A second dispatch's directory holding an unusable publication, beside the live
    # one. A reader that raised, or that stopped scanning, would take a working node's
    # visibility down with it — so the view must report the good one regardless.
    torn = _status_dir(dispatch_scratch) / "agent.activity"
    torn.write_text('{"run_id": "live-run", "round": "1", "node": "gh', encoding="utf-8")

    environment = _wrapper_environment(
        tmp_path,
        oneharness_bin,
        status_dir,
        ONEHARNESS_BIN_CODEX=str(_rejecting_codex(tmp_path / "rejecting-codex")),
        ONEHARNESS_HISTORY_LABELS="run_id=live-run,round=1,node=ship,persona=engineer",
    )

    with subprocess.Popen(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "oneharness-agent.sh"),
            "run",
            "--compact",
            "--events",
            "--prompt",
            "watch this turn",
        ],
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=environment,
    ) as process:
        try:
            limit = deadline(60)
            while time.monotonic() < limit and process.poll() is None:
                if _read_activity(status_dir / "agent.activity") is not None:
                    break
                time.sleep(0.05)
            assert process.poll() is None, "the turn ended before it could be observed"
            shown = subprocess.run(
                [
                    str(Path(oneharness_bin).with_name("orchestrator-status")),
                    "live-run",
                    "--runs-dir",
                    str(runs_dir),
                ],
                text=True,
                capture_output=True,
                # The reader finds a live dispatch's scratch directory the same way
                # the sweeper does, so it has to be told the same root.
                env={**os.environ, "TMPDIR": str(scratch), "ONEHARNESS_HISTORY_DIR": str(scratch)},
                timeout=timeout(60),
            )
        finally:
            process.kill()

    assert shown.returncode == 0, shown.stderr
    # The guarantee the journal join made, unchanged: dispatched, no completed turn.
    assert "No completed harness turns recorded for run live-run yet" in shown.stdout
    assert "round-01 ship engineer" in shown.stdout
    assert "no completed turn yet" in shown.stdout
    # And the part only a streamed turn can answer.
    assert "now Bash just check" in shown.stdout
    assert "event(s)," in shown.stdout


def test_the_filter_forwards_output_it_does_not_recognize(dispatch_scratch: ExitStack) -> None:
    """Whatever the child said reaches onejudge, even when this filter cannot read it.

    The filter sits between oneharness and onejudge on the one channel a turn's
    answer travels, so anything it drops is an answer that never arrives — a
    dispatch failure caused by the observability change rather than by the work. It
    is driven here at its real interface, the one the wrapper gives it: the child's
    stdout on stdin, onejudge's stdin on stdout, and the two files it writes.
    """
    status_dir = _status_dir(dispatch_scratch)
    record = status_dir / "agent.stdout"
    record.touch()
    activity = status_dir / "agent.activity"
    report = '{"schema_version":"0.3","results":[]}'
    stream = "\n".join(
        (
            # An envelope shape this build does not model — a later oneharness may
            # add one, and a turn must not die because of it.
            '{"type":"notice","message":"a shape from a later protocol"}',
            # An event envelope with no event in it: nothing to publish, and nothing
            # onejudge could do with it either.
            '{"type":"event"}',
            # Not JSON at all, which is what a harness that printed over the protocol
            # looks like.
            "oneharness: warning: something happened",
            '{"type":"event","event":{"kind":"tool_call","name":"Bash",'
            '"input":{"command":"just check"}}}',
            '{"type":"result","report":' + report + "}",
        )
    )

    completed = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python3"),
            str(REPO_ROOT / "scripts" / "oneharness-stream.py"),
            str(record),
            str(activity),
        ],
        input=stream + "\n",
        text=True,
        capture_output=True,
        env={**os.environ, "ONEHARNESS_HISTORY_LABELS": "run_id=r,round=1,node=n"},
        timeout=timeout(30),
    )

    assert completed.returncode == 0, completed.stderr
    forwarded = completed.stdout.strip().splitlines()
    # Everything unrecognized, verbatim and in order, then the unwrapped report.
    assert forwarded == [
        '{"type":"notice","message":"a shape from a later protocol"}',
        "oneharness: warning: something happened",
        report,
    ]
    # The raw record keeps every line the child wrote, recognized or not.
    assert record.read_text(encoding="utf-8").strip().splitlines() == stream.splitlines()
    # And the one real event still published, counted as the only one.
    assert json.loads(activity.read_text(encoding="utf-8"))["events"] == 1


def test_the_filter_reports_a_record_it_cannot_keep(dispatch_scratch: ExitStack) -> None:
    """`tee` failed the capture here before, and the wrapper still turns that into a
    failed turn — the one thing that must not happen is a turn whose transcript
    quietly went missing being reported as a turn nobody had anything to say about.
    """
    status_dir = _status_dir(dispatch_scratch)
    # A directory where the record goes is unopenable regardless of privilege.
    (status_dir / "agent.stdout").mkdir()

    completed = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python3"),
            str(REPO_ROOT / "scripts" / "oneharness-stream.py"),
            str(status_dir / "agent.stdout"),
            str(status_dir / "agent.activity"),
        ],
        input='{"type":"result","report":{}}\n',
        text=True,
        capture_output=True,
        timeout=timeout(30),
    )

    assert completed.returncode == 2
    assert "cannot keep the agent stdout record" in completed.stderr
    # The message names what to do about it, not only what went wrong.
    assert "retry through orchestrator dispatch" in completed.stderr


def test_the_filter_refuses_to_write_outside_a_dispatch_status_directory(
    tmp_path: Path,
) -> None:
    """A path is this process's one input, and both of its paths become writes.

    It appends a turn's whole stdout to one and atomically replaces the other, so an
    unvalidated path would make it a general-purpose writer pointed by its caller.
    It requires the same directory shape the wrapper requires of
    ``ORCHESTRATOR_AGENT_STATUS_DIR`` before writing any marker of its own.
    """
    stray = tmp_path / "somewhere" / "agent.stdout"
    stray.parent.mkdir(parents=True)

    completed = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python3"),
            str(REPO_ROOT / "scripts" / "oneharness-stream.py"),
            str(stray),
            str(stray.with_name("agent.activity")),
        ],
        input='{"type":"result","report":{}}\n',
        text=True,
        capture_output=True,
        timeout=timeout(30),
    )

    assert completed.returncode == 2
    assert "in one dispatch status directory" in completed.stderr
    assert "invoke through orchestrator dispatch" in completed.stderr
    assert not stray.exists()


def test_status_reports_only_the_publication_it_can_stand_behind(
    tmp_path: Path, dispatch_scratch: ExitStack
) -> None:
    """Every rejection the reader makes, through the command a planner actually runs.

    These files sit under a scratch root shared with every other dispatch on the
    host, and what they carry is printed as a statement about what a node is doing
    *now*. Each one below is a way that statement could be wrong rather than merely
    missing — and the failure mode they share is silence: a view that accepted one
    would look exactly as authoritative as this one does.
    """
    runs_dir = tmp_path / "runs"
    # llmlint: ignore[tests_mirror_real_usage] the journal is this view's own input
    journal = open_journal(runs_dir / "picky-run", RunId("picky-run"), 1)
    for node in ("ship", "nobodys", "bloated", "ancient", "ahead", "unreal", "numbered"):
        # llmlint: ignore[tests_mirror_real_usage] the recorded transition, via its own API
        journal.append("node-started", node=NodeId(node), detail={"persona": "engineer"})

    def summary(node: str, **overrides: object) -> str:
        return json.dumps(
            {
                "run_id": "picky-run",
                "round": "1",
                "node": node,
                "at": time.time(),
                "kind": "tool_call",
                "name": "Bash",
                "detail": f"just {node}",
                "events": 3,
                **overrides,
            }
        )

    # The one a live dispatcher is behind.
    (_status_dir(dispatch_scratch) / "agent.activity").write_text(summary("ship"), encoding="utf-8")
    # A watchdog-shaped directory nothing is dispatching into: no owner lock at all.
    unowned = tmp_path / "scratch" / "orchestrator-watchdog-nobodys" / "agent"
    unowned.mkdir(parents=True)
    (unowned / "agent.activity").write_text(summary("nobodys"), encoding="utf-8")
    # A file far past what a summary can be, whose first bytes would have parsed.
    (_status_dir(dispatch_scratch) / "agent.activity").write_text(
        summary("bloated") + "\n" + " " * MAX_SUMMARY_BYTES, encoding="utf-8"
    )
    # A turn that has long since moved on, and a clock this reader cannot reason about.
    (_status_dir(dispatch_scratch) / "agent.activity").write_text(
        summary("ancient", at=time.time() - STALE_AFTER_SECONDS - 60), encoding="utf-8"
    )
    (_status_dir(dispatch_scratch) / "agent.activity").write_text(
        summary("ahead", at=time.time() + STALE_AFTER_SECONDS + 60), encoding="utf-8"
    )
    # A JSON number Python parses and every age comparison silently passes.
    (_status_dir(dispatch_scratch) / "agent.activity").write_text(
        '{"run_id":"picky-run","round":"1","node":"unreal","at":NaN,"name":"Bash"}',
        encoding="utf-8",
    )
    # A round of digits alone, past the length `int` will convert: `isdigit` says yes
    # and the conversion raises, which is a crash rather than a wrong answer.
    (_status_dir(dispatch_scratch) / "agent.activity").write_text(
        summary("numbered", round="9" * 8000), encoding="utf-8"
    )

    shown = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "orchestrator-status"),
            "picky-run",
            "--runs-dir",
            str(runs_dir),
        ],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "TMPDIR": str(tmp_path / "scratch"),
            "ONEHARNESS_HISTORY_DIR": str(tmp_path / "history"),
        },
        timeout=timeout(60),
    )

    assert shown.returncode == 0, shown.stderr
    # Every node is still reported as dispatched and unfinished — that guarantee is
    # the journal's and none of this can touch it.
    for node in ("ship", "nobodys", "bloated", "ancient", "ahead", "unreal", "numbered"):
        assert f"round-01 {node} engineer" in shown.stdout
    # Exactly one of them says what it is doing.
    assert "now Bash just ship" in shown.stdout
    assert shown.stdout.count("event(s),") == 1
    for rejected in ("just nobodys", "just bloated", "just ancient", "just ahead", "just numbered"):
        assert rejected not in shown.stdout
