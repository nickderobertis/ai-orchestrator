"""The guards `scripts/oneharness-stream.py` applies to its own two arguments.

`scripts/oneharness-agent.sh` is the only caller and always hands it two files in
the status directory a dispatch created — so what these cover is what happens when
something else does not. A path is this filter's one input, and both of its paths
become writes: an append of a whole turn's stdout and an atomic replace. A filter
that would write wherever it was pointed is not one to hand an unvalidated path,
and neither refusal is reachable through the wrapper, which is exactly why they are
covered here rather than in a journey.

What the filter does with a *stream* is proven where it is production behavior:
through the wrapper, in `tests/test_oneharness_agent_wrapper.py`, and against the
real oneharness CLI in `tests/e2e/test_agent_stream_e2e.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT

FILTER = REPO_ROOT / "scripts" / "oneharness-stream.py"
INTERPRETER = REPO_ROOT / ".venv" / "bin" / "python3"


def _run(*paths: Path, stream: str = '{"type":"result","report":{}}\n') -> subprocess.Popen[str]:
    return subprocess.run(
        [str(INTERPRETER), str(FILTER), *(str(path) for path in paths)],
        input=stream,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_it_refuses_paths_outside_a_dispatch_status_directory(tmp_path: Path) -> None:
    stray = tmp_path / "somewhere" / "agent.stdout"
    stray.parent.mkdir(parents=True)

    completed = _run(stray, stray.with_name("agent.activity"))

    assert completed.returncode == 2
    assert "in one dispatch status directory" in completed.stderr
    assert "invoke through orchestrator dispatch" in completed.stderr
    assert not stray.exists()


def test_it_refuses_a_status_directory_that_resolves_somewhere_else(tmp_path: Path) -> None:
    """Judged by where writing lands, not by how the path is spelled."""
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "orchestrator-watchdog-sneaky").symlink_to(tmp_path / "elsewhere")
    status_dir = tmp_path / "orchestrator-watchdog-sneaky" / "agent"
    status_dir.mkdir()

    completed = _run(status_dir / "agent.stdout", status_dir / "agent.activity")

    assert completed.returncode == 2
    assert "in one dispatch status directory" in completed.stderr


def test_it_refuses_two_paths_from_different_dispatches(tmp_path: Path) -> None:
    """They describe one turn: a record kept beside another dispatch's activity would
    attribute this turn's work to that node.
    """
    first = tmp_path / "orchestrator-watchdog-one" / "agent"
    second = tmp_path / "orchestrator-watchdog-two" / "agent"
    for status_dir in (first, second):
        status_dir.mkdir(parents=True)

    completed = _run(first / "agent.stdout", second / "agent.activity")

    assert completed.returncode == 2
    assert "in one dispatch status directory" in completed.stderr


def test_it_refuses_a_file_it_is_not_meant_to_write(tmp_path: Path) -> None:
    """A valid directory must not become a way to write some other file in it."""
    status_dir = tmp_path / "orchestrator-watchdog-named" / "agent"
    status_dir.mkdir(parents=True)

    completed = _run(status_dir / "agent.done", status_dir / "agent.activity")

    assert completed.returncode == 2
    assert not (status_dir / "agent.done").exists()


def test_a_record_it_cannot_keep_fails_the_capture_and_says_so(tmp_path: Path) -> None:
    """`tee` failed the capture here before, and the wrapper still turns that into a
    failed turn — what must not happen is a turn whose transcript quietly went
    missing being reported as a turn nobody had anything to say about.
    """
    status_dir = tmp_path / "orchestrator-watchdog-unwritable" / "agent"
    status_dir.mkdir(parents=True)
    # A directory where the record goes is unopenable regardless of privilege.
    (status_dir / "agent.stdout").mkdir()

    completed = _run(status_dir / "agent.stdout", status_dir / "agent.activity")

    assert completed.returncode == 2
    assert "cannot keep the agent stdout record" in completed.stderr
    # The message names what to do about it, not only what went wrong.
    assert "retry through orchestrator dispatch" in completed.stderr


def test_it_says_what_it_expects_when_given_the_wrong_number_of_paths(tmp_path: Path) -> None:
    completed = _run(tmp_path / "orchestrator-watchdog-lonely" / "agent" / "agent.stdout")

    assert completed.returncode == 2
    assert "expected <stdout-record> <activity-file>" in completed.stderr
