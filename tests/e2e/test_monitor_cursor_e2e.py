"""A paced monitor carries its stream cursor across real turns, and writes no file doing it.

`personas/orchestrator.yaml` used to keep the monitor's cursor in two files in its working
directory. That directory is the launch directory — on this host a `local-direct`
publication checkout — and its next landing was refused over the `monitor.cursor` and
`monitor.batch` the persona had put there (issue #1004, root cause 4). The persona now
keeps the cursor in the conversation instead: every `onepipeline monitor` read ends in one
`-- cursor 1:<run>:<byte>` resume line, and the next turn hands that value to `--cursor`.

`tests/e2e/test_supervisory_prompt_discipline_e2e.py` holds the prose on both sides. This
journey runs it. One real `just orchestrate` launch, whose monitor's agent side is doubled
at the paid model and nothing below it: `fake_backend.py`'s `MONITOR_READS_ENV` branch runs
the `sh` block of that member's own effective system prompt, where the turn runs, against
the installed `onepipeline` and the run's real `detailed` filter, and carries each turn's
resume line into the next.

What a resumed turn rendered is read against the store itself rather than against another
cursor read. The run's directory is copied, its `events.jsonl` cut at each turn's resume
byte, and the installed verb renders every cut with no cursor at all — so what was recorded
between two turns is the difference between two of those renders. The full render cannot
stand in for it: it orders the merged stream by timestamp, while the observer's own events
reach the store in batches carrying earlier timestamps than events already there.

Those batches are also why a quiet turn is common inside a live launch but never certain:
whether anything reaches the store between two turns depends on when the next batch
arrives. So the quiet turn this journey rests on, and the turn that resumes from it, are
further turns of the same member — the doubled harness invocation, session, working
directory and environment its live turns recorded — taken once the run, and its store with
it, has stopped. A live quiet turn is held to the same checks as every other turn.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from fake_backend import (
    AGENT_DELAY_ENV,
    MONITOR_READS_ENV,
    MONITOR_REFUSED_TURNS_ENV,
    REFUSED_CURSOR,
    MonitorRead,
    monitor_reads,
)
from project_fixtures import project_from_plan
from published_surface import surface_of
from test_orchestrate_launch_e2e import MONITOR_MEMBER
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The stand-in the launch's harness turns reach, run again directly for the turns taken
#: after the run has stopped.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"

#: The binary this checkout installed, which every turn's read has to have been.
PINNED_ONEPIPELINE = (REPO_ROOT / ".venv" / "bin" / "onepipeline").resolve()

#: The plan the launch runs, and the run id `onepipeline` mints from its name. One node,
#: held open long enough for the monitor to take every live turn this journey reads.
LAUNCHED_RUN = "monitor-cursor"
HELD_NODE = "held"
HELD_SECONDS = 20

#: The run's store, in the run's own directory, that a cursor's byte counts into.
STORE = "events.jsonl"

#: The profile the monitor's persona reads through: the whole merged stream.
DETAILED_PROFILE = "detailed"

#: The monitor's hold between turns for this launch. The shipped five minutes would allow
#: one turn in a run this long; `--set members.monitor.schedule.every` is the published
#: override for exactly that, and the shipped value stays what it is.
MONITOR_HOLD_SECONDS = 2

#: Which live turn is handed a cursor the verb refuses, and so how many live turns the
#: journey needs: a bounded tail, a turn resumed from it, the refused turn's fallback, and
#: a turn resumed from that fallback's resume line.
REFUSED_TURN = 3
LIVE_TURNS_NEEDED = REFUSED_TURN + 1

#: How many turns may be taken against the stopped run before one of them has to be quiet.
#: The first reads whatever landed as the run stopped; the one after it has nothing to read.
STOPPED_TURNS_TO_QUIET = 4

#: Where `oneagentgraph` writes the launch's graph state, named so it is this run's own.
GRAPH_STATE_ENV = "ONEAGENTGRAPH_STATE_DIR"

#: One rendered event, the one resume line a read ends with, the refusal the binary puts
#: on stderr, and the status the prompt's fallback echoes for it.
EVENT_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+Z\s")
RESUME_LINE = re.compile(r"^-- cursor (1:[^:\s]+:\d+)$")
REFUSAL = "onepipeline: invalid:"
ECHOED_STATUS = re.compile(r"\(exit (\d+)\)")

#: What `oneharness` says when a turn's `--control` socket address is past the platform's
#: limit. A launch printing it took every controlled turn twice, the second uncontrolled.
SOCKET_REFUSAL = "unix-socket address limit"


def test_the_installed_monitor_verb_resumes_from_a_cursor() -> None:
    """The verb the persona's read spells takes `--cursor`, on the binary this checkout installed.

    The persona has the monitor carry `--cursor` from one turn to the next and keeps no
    file for it, so a release without the option leaves every turn refused — and falling
    back to a bounded tail on every turn is the skipped stream the cursor exists to prevent.
    """
    surface = surface_of("onepipeline")
    assert ("monitor",) in surface.paths, "the installed onepipeline has no `monitor` verb"
    assert surface.accepts(("monitor",), "--cursor"), (
        "the installed `onepipeline monitor` takes no `--cursor`, so the monitor's paced "
        "read in personas/orchestrator.yaml is refused on every turn"
    )


def _files_held(directory: Path) -> dict[str, str]:
    """What git reports `directory` holds beyond its commit: tracked-and-changed and untracked.

    Each entry is its status code and path, mapped to git's digest of the content, so a file
    added, removed, or rewritten between two reads makes them differ. Git reads the content
    rather than this process: what is compared is whether the directory changed, which no
    file's own content decides.
    """
    reported = _git(directory, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    held: dict[str, str] = {}
    entries = iter(reported.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if code[0] in "RC":
            next(entries, None)
        held[f"{code} {path}"] = (
            _git(directory, "hash-object", "--", path).strip()
            if (directory / path).is_file()
            else "absent"
        )
    return held


def _git(directory: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=directory, text=True, capture_output=True, check=True
    ).stdout


def _events(output: str) -> list[str]:
    """The event lines a read rendered, in order."""
    return [line for line in output.splitlines() if EVENT_LINE.match(line)]


def _resume_line(read: MonitorRead) -> str:
    """The cursor the read's one resume line carries, which has to be its last line."""
    lines = read["output"].rstrip("\n").splitlines()
    resumes = [line for line in lines if line.startswith("-- cursor")]
    last = RESUME_LINE.match(lines[-1]) if lines else None
    assert len(resumes) == 1 and last is not None, (
        f"monitor turn {read['turn']} did not end in exactly one resume line:\n{read['output']}"
    )
    return last.group(1)


def _refused(read: MonitorRead) -> bool:
    return REFUSAL in read["output"]


def _rendered(read: MonitorRead) -> list[str]:
    """What the turn's read rendered: after a refusal, what its bounded tail rendered."""
    output = read["output"]
    if _refused(read):
        before, _, output = output.partition(REFUSAL)
        assert not _events(before), (
            f"monitor turn {read['turn']}'s refused read rendered events before its "
            f"refusal:\n{read['output']}"
        )
    return _events(output)


def _stream_as_of(
    runs: Path, recorded: bytes, cursor: str, environment: dict[str, str]
) -> Counter[str]:
    """Every event the run's stream held at `cursor`'s byte, rendered with no cursor.

    `runs` is a copy of the run's directory, whose store is cut to the byte the cursor
    counts to — the byte of `events.jsonl` on the installed release, which every pair this
    feeds re-takes, because a cut at the wrong byte renders a difference no turn rendered.
    """
    # llmlint: ignore[tests_mirror_real_usage] The oracle has to reach the stream without a
    # cursor, or it would check a cursor read against a cursor read; no operator view renders
    # a run as of a byte, so a copy of the store is cut there and the real verb renders it.
    (runs / LAUNCHED_RUN / STORE).write_bytes(recorded[: int(cursor.rsplit(":", 1)[1])])
    rendered = subprocess.run(
        [str(PINNED_ONEPIPELINE), "monitor", LAUNCHED_RUN, "--filter", DETAILED_PROFILE],
        cwd=runs,
        env={**environment, "ONEPIPELINE_RUNS_DIR": str(runs)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert rendered.returncode == 0, rendered.stderr
    return Counter(_events(rendered.stdout))


def _take_a_stopped_turn(read: MonitorRead, environment: dict[str, str]) -> None:
    """Take one more turn of the member `read` was a turn of, as its harness invocation.

    The recorded argv, session and environment, spawned the way `oneagentgraph` spawns the
    member's harness: the process starts in the member's scratch directory, and the argv's
    recorded `--cwd` — the launch directory — is still where the turn's read runs.
    """
    config = next(argument for argument in read["argv"] if argument.endswith("oneharness.toml"))
    member = {**environment, **{k: v for k, v in read["environment"].items() if v is not None}}
    # llmlint: ignore[tests_mirror_real_usage] A quiet turn is never certain while the run is
    # live, so the turns that prove it are taken after the stop, as the member's own recorded
    # harness invocation — the arrangement this journey's plan ruled for that clause.
    taken = subprocess.run(  # noqa: S603 - the member's own harness invocation
        [str(FAKE_BACKEND), *read["argv"]],
        cwd=Path(config).parent,
        env=member,
        input="The run has stopped. Take your next turn.",
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert taken.returncode == 0, (
        f"a monitor turn against the stopped run failed:\n{taken.stdout}\n{taken.stderr}"
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `xdist_group` selects
# an xdist worker under this suite's `--dist loadgroup`, not a test tier; the tiers here
# split by what a test reads, which is what each one's Nx cache key has to cover.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] Same site: this journey
# joins the `tests/e2e` tree its launch siblings already sit in, and which Nx project owns
# that tree is a property of the tree rather than of this module — re-homing it is
# enforcement configuration this change may not move in order to pass.
# llmlint: ignore-block[shell_test_tiers_stay_split] Same site, same reason; and this is a
# pytest journey over the real recipe, not a shell test suite.
@pytest.mark.xdist_group("monitor-cursor")
def test_a_monitor_carries_its_cursor_across_turns_and_writes_no_file(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Each turn reads from the resume line the turn before it ended with, and nothing else.

    Four claims the persona makes, each read off the turns that really happened:

    * a turn resumed from the previous turn's resume line renders nothing that turn
      rendered and every event recorded between them — read as the difference between the
      store rendered as of each of the two resume lines;
    * a turn handed a cursor the verb refuses gets a non-zero refusal, and its bounded tail
      ends in a resume line the next turn resumes from;
    * a turn with nothing new is quiet, still ends in a resume line, and the next turn
      resumes from that;
    * the member's working directory — the launch directory — holds exactly the files it
      held before the launch.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    environment = _launched_environment(tmp_path, oneharness_bin)
    reads_log = tmp_path / "monitor-reads.jsonl"
    # llmlint: ignore[e2e_not_mocked] Only the paid model's decisions are substituted: which
    # command a turn runs is read off its own prompt, and the command is the real one.
    environment[MONITOR_READS_ENV] = str(reads_log)
    # llmlint: ignore[tests_mirror_real_usage] Which cursor a turn passes is the paid model's
    # choice, the one thing doubled; the verb's refusal and the fallback it takes are real.
    environment[MONITOR_REFUSED_TURNS_ENV] = str(REFUSED_TURN)
    environment[AGENT_DELAY_ENV] = str(HELD_SECONDS)
    environment[GRAPH_STATE_ENV] = str(tmp_path / "graph-state")
    plan = tmp_path / "cursor.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "name": LAUNCHED_RUN,
                "goal": {"text": "prove a paced monitor carries its cursor and keeps no file"},
                "tasks": [
                    {
                        "id": HELD_NODE,
                        "persona": "engineer",
                        "task": "## What\nReport.\n\n## Why\nBecause.\n\n"
                        "## Acceptance criteria\n- Reported.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    held_before = _files_held(REPO_ROOT)

    printed = tmp_path / "launch.log"
    with printed.open("w", encoding="utf-8") as streaming:
        launch = subprocess.Popen(  # noqa: S603 - the real recipe, as an operator runs it
            [
                "just",
                "orchestrate",
                project_from_plan(plan),
                "--set",
                f"members.{MONITOR_MEMBER}.schedule.every={MONITOR_HOLD_SECONDS}",
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=streaming,
            stderr=subprocess.STDOUT,
        )
        try:
            launch.wait(timeout=e2e_timeout(300))
        finally:
            launch.kill()
            launch.wait(timeout=e2e_timeout(60))
            subprocess.run(
                ["just", "stop", LAUNCHED_RUN],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=e2e_timeout(60),
                check=False,
            )

    # llmlint: ignore-block[tests_mirror_real_usage] What a turn ran and what it read back
    # is on no operator view: a monitor's tool output is not in the planner's stream, so
    # the stand-in's record of each turn is the only witness, as `_monitor_prompts` is for
    # a turn's prompt in the sibling journey.
    live = monitor_reads(reads_log)
    assert len(live) >= LIVE_TURNS_NEEDED, (
        f"the monitor took {len(live)} read turn(s) and this journey needs "
        f"{LIVE_TURNS_NEEDED}:\n{printed.read_text('utf-8')}"
    )
    assert SOCKET_REFUSAL not in printed.read_text("utf-8"), (
        "a monitor turn's control socket was refused, so its turns ran twice and the turns "
        f"against the stopped run replay whichever came last:\n{printed.read_text('utf-8')}"
    )
    for _ in range(STOPPED_TURNS_TO_QUIET):
        _take_a_stopped_turn(live[-1], environment)
        if not _rendered(monitor_reads(reads_log)[-1]):
            break
    _take_a_stopped_turn(live[-1], environment)
    reads = monitor_reads(reads_log)
    # Live and stopped turns alike: one conversation, the launched run, the installed
    # binary, and the launch directory whose files are held below.
    assert len({read["session"] for read in reads}) == 1, (
        f"the monitor's turns arrived as more than one conversation: {reads}"
    )
    for read in reads:
        assert read["status"] is not None, read["output"]
        assert read["environment"]["ONEPIPELINE_RUN_ID"] == LAUNCHED_RUN, read["environment"]
        assert read["onepipeline"] == str(PINNED_ONEPIPELINE), (
            f"monitor turn {read['turn']} read through {read['onepipeline']}, not the "
            f"installed {PINNED_ONEPIPELINE}"
        )
        assert read["cwd"] is not None and Path(read["cwd"]).resolve() == REPO_ROOT.resolve(), (
            f"monitor turn {read['turn']} ran in {read['cwd']}, not the launch directory "
            f"{REPO_ROOT} whose files this journey holds"
        )
    # llmlint: ignore-end[tests_mirror_real_usage]
    held_after = _files_held(REPO_ROOT)

    runs = tmp_path / "as-recorded"
    shutil.copytree(Path(environment["ONEPIPELINE_RUNS_DIR"]), runs)
    recorded = (runs / LAUNCHED_RUN / STORE).read_bytes()

    # Every turn either resumed from the resume line the turn before it ended with, or was
    # refused and read the bounded tail — the first turn, with no cursor, being refused.
    first = reads[0]
    assert first["cursor"] == "" and _refused(first), (
        f"the monitor's first turn did not read the bounded tail for want of a cursor:\n"
        f"{first['output']}"
    )
    for earlier, read in zip([None, *reads], reads, strict=False):
        resumed_at = _resume_line(read)
        if earlier is None or _refused(read):
            assert _refused(read), read["output"]
            continue
        assert read["cursor"] == _resume_line(earlier), (
            f"monitor turn {read['turn']} read from {read['cursor']!r}, not the resume line "
            f"{_resume_line(earlier)!r} its previous turn ended with"
        )
        overlap = set(_rendered(read)) & set(_rendered(earlier))
        assert not overlap, (
            f"monitor turn {read['turn']} re-rendered what turn {earlier['turn']} had already "
            f"read: {sorted(overlap)}"
        )
        before = _stream_as_of(runs, recorded, read["cursor"], environment)
        after = _stream_as_of(runs, recorded, resumed_at, environment)
        assert not before - after and after - before == Counter(_rendered(read)), (
            f"monitor turn {read['turn']} did not render exactly the events recorded between "
            f"{read['cursor']} and {resumed_at}: it rendered {_rendered(read)}, and the store "
            f"gained {sorted((after - before).elements())}"
        )
    # A live turn often renders nothing, because the store takes the observer's events in
    # batches; the pairs above are only evidence of resumption if some resumed turn read a
    # non-empty stretch of the store.
    resumed = [read for read in live[1:] if not _refused(read)]
    assert any(_rendered(read) for read in resumed), (
        "no live turn resumed from a resume line rendered an event, so no pair above read "
        "a stretch of the store with anything in it:\n"
        + "\n---\n".join(read["output"] for read in resumed)
    )

    refused = reads[REFUSED_TURN - 1]
    echoed = ECHOED_STATUS.search(refused["output"])
    assert refused["cursor"] == REFUSED_CURSOR and _refused(refused), refused["output"]
    assert echoed is not None and int(echoed.group(1)) != 0, (
        f"the refused cursor did not come back as a non-zero refusal:\n{refused['output']}"
    )
    after_refusal = reads[REFUSED_TURN]
    assert after_refusal["cursor"] == _resume_line(refused) and not _refused(after_refusal), (
        f"the turn after a refused cursor did not resume from the resume line its bounded "
        f"tail ended with:\n{refused['output']}\n---\n{after_refusal['output']}"
    )

    stopped = reads[len(live) :]
    quiet = next((index for index, read in enumerate(stopped[:-1]) if not _rendered(read)), None)
    assert quiet is not None, (
        "no turn against the stopped run was quiet, so the quiet turn is unproven:\n"
        + "\n---\n".join(read["output"] for read in stopped)
    )
    assert not _refused(stopped[quiet]) and stopped[quiet]["status"] == 0, stopped[quiet]
    following = stopped[quiet + 1]
    assert (
        following["cursor"] == _resume_line(stopped[quiet])
        and not _refused(following)
        and following["status"] == 0
    ), (
        f"the turn after a quiet turn did not resume from the resume line the quiet turn "
        f"ended with:\n{stopped[quiet]['output']}\n---\n{following['output']}"
    )

    assert held_after == held_before, (
        "the monitor's working directory does not hold the files it held before the launch: "
        f"before {held_before}, after {held_after}"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
# llmlint: ignore-end[shell_test_tiers_stay_split]
