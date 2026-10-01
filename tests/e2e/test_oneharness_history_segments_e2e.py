"""The installed `oneharness` CLI records and reads history without scanning the store.

Every dispatch here stalled behind one history lock while each recorded run re-read a
1.36 GB legacy index and walked tens of thousands of session files. The adopted CLI
(`config/oneharness.version`) records into dated pointer segments under `.index.d/`,
and these journeys hold it to that on the real binary rather than on its changelog:

* recording a run into a store whose legacy `.index.jsonl` and `.event-index.jsonl`
  cannot be opened and which holds a thousand other sessions' files touches nothing
  but the run's own session file, its project directory, `.index.d/` and the two
  segments for the run's UTC date — read off a syscall trace of the whole process
  tree, so a walk, an index read, or a failed legacy open caught and answered by a
  scan all fail, whether or not the open succeeded;
* a store written by a released pre-cutover CLI (`tests/fixtures/pre-cutover-history/`)
  stays readable with no conversion: `history show <id> --all-time` finds each run,
  so does a pointer line's `history show <session> --project <dir>`, and no file in
  the store changes.

The paid provider is the one double: oneharness's own shipped mock responder, scripted
through `MOCK_STDOUT` with a turn that spends tokens and calls a tool, because a turn
reporting no telemetry is one the CLI declines to record at all.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest
from mock_oneharness import mock_run_command
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A history store a released pre-cutover `oneharness` wrote. `store/` is exactly what the
#: released `oneharness-cli` 0.20.0 — linking `oneharness-core` 0.22.0, the last core
#: before dated `.index.d/` segments — left after two recorded runs: each session file
#: under its project slug, the legacy `.index.jsonl` and `.index.lock`, and no
#: `.index.d/`. `pointers.jsonl` is the pointer file the same runs appended
#: (`--history-pointer-file`), the capturing store's absolute path replaced by
#: `HISTORY_DIR_PLACEHOLDER`. Captured hermetically, the paid provider replaced by
#: oneharness's own mock responder scripted with a turn that reports usage and one tool
#: call, from a project at the fixed path `/tmp/ai-orchestrator-pre-cutover-project` so
#: no slug names the capturing host:
#:
#:     oneharness run --no-config --harness claude-code --mock-harness claude-code \
#:       --events --history --history-dir <store> --history-pointer-file <pointers> \
#:       --prompt "<first|second> run before the cutover" --format json --compact
#:
#: Re-capture it only against another pre-cutover release. It carries no README of its
#: own because the recipe tier's key reads `tests/fixtures/` whole while the code key
#: excludes every Markdown file (`tests/test_nx_cache_scope.py`).
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "pre-cutover-history"
#: Where the fixture's pointer file names the store it was captured into.
HISTORY_DIR_PLACEHOLDER = "{{HISTORY_DIR}}"
LEGACY_INDEXES = (".index.jsonl", ".event-index.jsonl")
#: The criterion's floor on how large a store the run must not walk.
OTHER_SESSIONS = 1000
#: A claude-code stream that reports usage and one tool call, so the run is recorded
#: and the events segment is written too.
TURN = "\n".join(
    json.dumps(line)
    for line in (
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "model": "stand-in",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "true"}}
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
            "session_id": "stand-in-session",
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "recorded",
            "session_id": "stand-in-session",
            "total_cost_usd": 0.01,
            "usage": {"input_tokens": 3, "output_tokens": 2},
        },
    )
)

#: `open`/`creat` name the path first; `openat`/`openat2` name a directory fd first,
#: which `strace -y` decodes as `<path>` after it.
OPENED = re.compile(
    r"\b(?:open|creat)\(\"(?P<path>[^\"]*)\""
    r"|\bopenat2?\((?:AT_FDCWD|\d+)(?:<(?P<dir>[^>]*)>)?, \"(?P<rel>[^\"]*)\""
)
LISTED = re.compile(r"\bgetdents(?:64)?\(\d+<(?P<path>[^>]*)>")


def _strace() -> str:
    found = shutil.which("strace")
    if found is None:
        pytest.fail(
            "strace is not installed; these journeys read every open and listing the "
            "oneharness process tree makes from it, and cannot prove the store unwalked "
            "without one"
        )
    return found


def _environment(home: Path) -> dict[str, str]:
    """Built from nothing, so no inherited `ONEHARNESS_*` reaches the CLI."""
    return {
        "HOME": str(home),
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "PYTHONPATH": str(REPO_ROOT / "tests" / "e2e"),
    }


def _traced(
    command: list[str], *, trace: Path, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [_strace(), "-f", "-y", "-qq", "-e", "trace=%file,getdents64", "-o", str(trace), *command],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
    )


def _touched(trace: Path, store: Path) -> set[Path]:
    """Every path inside `store` the traced tree opened or listed, success or not."""
    touched: set[Path] = set()
    for line in trace.read_text(encoding="utf-8").splitlines():
        for match in OPENED.finditer(line):
            if match["path"] is not None:
                path = Path(match["path"])
            else:
                path = Path(match["rel"])
                if not path.is_absolute():
                    path = Path(match["dir"] or "/") / path
            touched.add(Path(os.path.normpath(path)))
        for match in LISTED.finditer(line):
            touched.add(Path(os.path.normpath(match["path"])))
    return {path for path in touched if path == store or store in path.parents}


@dataclass(frozen=True)
class Snapshot:
    """A file's bytes and the metadata a write would move."""

    content: bytes
    size: int
    mtime_ns: int


def _snapshot(paths: list[Path]) -> dict[Path, Snapshot]:
    return {
        path: Snapshot(path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in paths
    }


def _store_with_unopenable_legacy_indexes(store: Path) -> tuple[list[Path], dict[Path, bytes]]:
    """A thousand released-shape sessions and two legacy indexes nothing may open.

    Each session line is one the released pre-cutover CLI wrote, so the store is the
    shape a walk would have to read. Returns the session files and each legacy
    index's bytes, taken before the index is made unopenable.
    """
    released = sorted((FIXTURE / "store").glob("*/*.jsonl"))
    sessions = []
    for number in range(OTHER_SESSIONS):
        project = store / f"other-project-{number % 25}"
        project.mkdir(parents=True, exist_ok=True)
        session = project / f"earlier-run-{number}-20250101T000000Z-{number}.jsonl"
        session.write_bytes(released[number % len(released)].read_bytes())
        sessions.append(session)
    legacy = {}
    for name in LEGACY_INDEXES:
        index = store / name
        index.write_bytes((FIXTURE / "store" / ".index.jsonl").read_bytes())
        legacy[index] = index.read_bytes()
        index.chmod(0)
    return sessions, legacy


# These two journeys are neither slow nor external: the provider is oneharness's own
# mock, nothing reaches a network, and the whole module runs in well under a second.
# They sit in `orchestrator:test` beside
# tests/e2e/test_oneharness_bin_override_e2e.py, which drives the same installed CLI
# there, so a project of their own would isolate no cost from any change.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see the note above
def test_recording_a_run_reads_no_index_and_walks_no_store(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Record, then read back, through the installed CLI; only the run's own paths open."""
    store = tmp_path / "history"
    project = tmp_path / "project"
    project.mkdir()
    sessions, legacy = _store_with_unopenable_legacy_indexes(store)
    for index in legacy:
        with pytest.raises(PermissionError):
            index.open("rb")
    before = _snapshot(sessions)
    legacy_stat = {index: index.stat() for index in legacy}
    env = {**_environment(tmp_path), "MOCK_STDOUT": TURN}

    run = _traced(
        mock_run_command(
            oneharness_bin,
            "--no-config",
            "--harness",
            "claude-code",
            "--events",
            "--history",
            "--history-dir",
            str(store),
            "--prompt",
            "record this run",
            "--format",
            "json",
            "--compact",
            harnesses=("claude-code",),
        ),
        trace=tmp_path / "run.trace",
        cwd=project,
        env=env,
    )

    assert run.returncode == 0, run.stderr
    assert "could not write history record" not in run.stderr, run.stderr
    report = json.loads(run.stdout)
    assert report["results"][0]["status"] == "ok", report["results"]
    assert report["history_file"], f"no history was recorded: {run.stderr}"
    session_file = Path(report["history_file"])
    assert session_file.is_file(), f"no history was recorded: {report['history_file']}"
    (record,) = (
        line
        for line in map(json.loads, session_file.read_text(encoding="utf-8").splitlines())
        if line.get("type") == "run"
    )
    history_id = record["history_id"]
    date = record["timestamp"][: len("YYYY-MM-DD")]
    segments = store / ".index.d"
    runs_segment = segments / f"runs-{date}.ndjson"
    events_segment = segments / f"events-{date}.ndjson"
    assert _touched(tmp_path / "run.trace", store) <= {
        session_file,
        session_file.parent,
        segments,
        runs_segment,
        events_segment,
    }, "recording a run opened or listed a path outside its own session and segments"
    assert runs_segment.is_file(), f"no runs segment for {date}: {sorted(store.glob('.*'))}"
    indexed = [json.loads(line) for line in runs_segment.read_text(encoding="utf-8").splitlines()]
    assert [entry["history_id"] for entry in indexed] == [history_id], indexed
    assert events_segment.read_text(encoding="utf-8").strip(), (
        "the doubled turn emitted events, and none reached the events segment"
    )

    show = _traced(
        [
            oneharness_bin,
            "history",
            "show",
            history_id,
            "--no-config",
            "--history-dir",
            str(store),
            "--format",
            "json",
            "--compact",
        ],
        trace=tmp_path / "show.trace",
        cwd=project,
        env=_environment(tmp_path),
    )

    assert show.returncode == 0, show.stderr
    assert [shown["history_id"] for shown in json.loads(show.stdout)] == [history_id]
    assert _touched(tmp_path / "show.trace", store) <= {runs_segment, session_file}, (
        "`history show <id>` opened or listed more than its date's runs segment and the "
        "session file"
    )
    assert not (store / ".index.lock").exists(), "a legacy index lock was created"
    assert _snapshot(sessions) == before, "another session's file changed"
    for index, content in legacy.items():
        after = index.stat()
        assert (after.st_size, after.st_mtime_ns, after.st_ino, stat.S_IMODE(after.st_mode)) == (
            legacy_stat[index].st_size,
            legacy_stat[index].st_mtime_ns,
            legacy_stat[index].st_ino,
            0,
        ), f"{index.name} changed"
        index.chmod(0o600)
        assert index.read_bytes() == content, f"{index.name} changed"


def test_a_store_a_released_core_wrote_stays_readable_with_no_conversion(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The pre-cutover fixture, read through the adopted CLI by id and by pointer line."""
    store = tmp_path / "store"
    shutil.copytree(FIXTURE / "store", store)
    pointer_file = tmp_path / "oneharness-sessions.jsonl"
    pointer_file.write_text(
        (FIXTURE / "pointers.jsonl")
        .read_text(encoding="utf-8")
        .replace(HISTORY_DIR_PLACEHOLDER, str(store)),
        encoding="utf-8",
    )
    files = sorted(path for path in store.rglob("*") if path.is_file())
    before = _snapshot(files)
    legacy = [
        json.loads(line)["record"]
        for line in (store / ".index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(legacy) == 2, legacy
    assert not (store / ".index.d").exists(), "the fixture is not a pre-cutover store"
    env = _environment(tmp_path)

    def oneharness(*args: str) -> list[dict[str, object]]:
        completed = subprocess.run(
            [oneharness_bin, "history", *args, "--format", "json", "--compact"],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(30),
        )
        assert completed.returncode == 0, completed.stderr
        shown = json.loads(completed.stdout)
        listed: list[dict[str, object]] = shown["pointers"] if isinstance(shown, dict) else shown
        return listed

    for record in legacy:
        # Outside the default window by construction: the dated index never held it, so
        # the bare read refuses and names the flag the docs send a reader to.
        bare = subprocess.run(
            [oneharness_bin, "history", "show", str(record["history_id"])]
            + ["--history-dir", str(store), "--format", "json", "--compact"],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(30),
        )
        assert bare.returncode != 0, bare.stdout
        assert "--all-time" in bare.stderr, bare.stderr
        shown = oneharness(
            "show", str(record["history_id"]), "--all-time", "--history-dir", str(store)
        )
        assert [(run["history_id"], run["session"]) for run in shown] == [
            (record["history_id"], record["session"])
        ]

    pointers = oneharness("pointers", str(pointer_file))
    assert sorted(str(pointer["history_id"]) for pointer in pointers) == sorted(
        str(record["history_id"]) for record in legacy
    )
    for pointer in pointers:
        shown = oneharness(
            "show",
            str(pointer["history_session"]),
            "--project",
            str(pointer["project"]),
            "--history-dir",
            str(pointer["history_dir"]),
        )
        assert [run["history_id"] for run in shown] == [pointer["history_id"]]

    assert sorted(path for path in store.rglob("*") if path.is_file()) == files, (
        "reading the pre-cutover store created a file in it"
    )
    assert _snapshot(files) == before, "reading the pre-cutover store changed a file in it"


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


class DefaultWindow(NamedTuple):
    """One `oneharness history` read's default window, as the docs and the CLI state it."""

    #: The `history` subcommand.
    verb: str
    #: The sentence each doc in `WINDOW_DOCS` carries about it.
    promise: str
    #: What the installed CLI's own `--help` says about it, the contract's one source.
    help_says: str
    #: The options the docs send a reader to for an earlier record, which that help
    #: has to offer.
    reaches_earlier: tuple[str, ...]


DEFAULT_WINDOWS = (
    DefaultWindow(
        verb="watch",
        promise="with no `--after` starts at the current UTC day",
        help_says="Today's records are emitted first",
        reaches_earlier=("--since <YYYY-MM-DD>", "--all-time"),
    ),
    DefaultWindow(
        verb="list",
        promise="reads the last seven UTC days",
        help_says="Reads the dated index for the last 7 UTC days",
        reaches_earlier=("--since <YYYY-MM-DD>", "--all-time"),
    ),
    DefaultWindow(
        verb="show",
        promise="is found only by `history show <history-id> --all-time`",
        help_says="finds a <history-id> the dated index does not hold",
        reaches_earlier=("--all-time",),
    ),
)
WINDOW_DOCS = ("docs/orchestration.md", "docs/telemetry.md")


# A `--help` read of the installed CLI per case, sub-second in all, and placed in the
# whole-workspace `orchestrator:test-docs` tier by `reads_docs` because it reconciles
# this repository's prose, which is the tier every such reconciliation here runs in.
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see the note above
@pytest.mark.reads_docs
@pytest.mark.parametrize("window", DEFAULT_WINDOWS, ids=lambda window: window.verb)
@pytest.mark.parametrize("doc", WINDOW_DOCS)
def test_the_docs_state_the_default_windows_the_installed_cli_has(
    oneharness_bin: str, doc: str, window: DefaultWindow
) -> None:
    """Each doc sending a reader to `oneharness history` states that verb's real window."""
    text = " ".join((REPO_ROOT / doc).read_text(encoding="utf-8").split())
    usage = subprocess.run(
        [oneharness_bin, "history", window.verb, "--help"],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=True,
    ).stdout

    assert window.help_says in " ".join(usage.split()), (
        f"the installed oneharness no longer says {window.help_says!r} about "
        f"`history {window.verb}`: re-read its window and the sentence {doc} states about it"
    )
    for option in window.reaches_earlier:
        assert option in usage, f"`oneharness history {window.verb}` no longer takes {option}"
    assert window.promise in text, (
        f"{doc} never states {window.promise!r} about `oneharness history {window.verb}`"
    )


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
