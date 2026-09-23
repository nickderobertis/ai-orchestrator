"""`scripts/ask-manager.sh` is the engine's `onepipeline ask`, and decides nothing itself.

The adapter is what `ORCHESTRATOR_ASK_MANAGER` names, so a dispatch has one executable path
to ask through; everything a question is — its three input forms, its frame on the run's
channel, the reply window, the one JSON line that answers — is the engine verb's. So this
module holds the adapter to being transparent: every argument reaches the verb in order,
stdin reaches it unread, and the verb's stdout and exit status are the adapter's own. That
half runs the real adapter from a mirror checkout whose locked `onepipeline` is a recording
stand-in, because only a recorder can say what reached it. The other half asks through the
real adapter into the installed engine over a real channel, with `--file` and on stdin,
and reads each frame's text back off the channel with the bus's own `next`.

`tests/ask_seam/ask_manager/test_ask_manager_e2e.py` drives the same adapter over a run
`just orchestrate` really launched, with a manager answering.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] One
Nx project: the `reads_recipes` marker moves these journeys between targets of the *same*
project, which is the mechanism `tests/conftest.py` documents and enforces — it refuses a marked
journey that reads anything its tier's key does not cover, and `tests/test_nx_cache_scope.py`
holds the selectors to a partition of the suite — rather than a way around a project
boundary; the host-tool journeys that spend a launch are the ones `tests/ask_seam/` splits
into projects of their own.

llmlint: ignore-file[tests_mirror_real_usage] The two real-channel journeys ask on a run
root `tests/e2e/probe_run_root.py` builds rather than one a launch wrote, because what
they read is the channel the engine writes and a launch would spend a whole run to give
them a launch record. That builder's records are reconciled field by field against the
installed engine by `tests/test_engine_contracts.py`, and
`tests/ask_seam/ask_manager/test_ask_manager_e2e.py` asks through the same adapter on runs
`just orchestrate` really launched.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple, TypedDict

import pytest
from probe_run_root import run_root
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_recipes

#: The real adapter, and the configuration the run's channel is read back under.
SHIM = REPO_ROOT / "scripts" / "ask-manager.sh"
CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"
BUS = REPO_ROOT / ".venv" / "bin" / "onemessagebus"

RUN = "shim-run-1"

#: What the stand-in answers, byte for byte, which the adapter must hand back unchanged.
STUB_ANSWER = '{"answer":"reply","correlation":"c-1","reply":{"message":"main"}}\n'

#: Arguments the verb reads, in an order and with spellings the adapter must not touch:
#: flags with their values, an argument carrying a space and one carrying a newline, and
#: after the end-of-options marker an option-shaped word and an empty argument.
ARGUMENTS = [
    "--about",
    "node-a",
    "--timeout",
    "45",
    "two words",
    "line\nbreak",
    "--",
    "--not-an-option",
    "",
]

#: Stdin the adapter must pass on without reading: a NUL, bytes that are not UTF-8, and no
#: trailing newline.
RAW_STDIN = b"question\x00with a nul \xff\xfe and no newline"

#: A question carrying every byte JSON reserves, a control, and non-ASCII text.
AWKWARD_QUESTION = 'back\\slash "quoted"\nsecond line\r\ttabbed \x01 ünï 日本\n'

STUB = """\
#!{python}
import json, os, sys
record = {{"argv": sys.argv[1:], "stdin": sys.stdin.buffer.read().hex()}}
with open(os.environ["SHIM_STUB_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(record) + "\\n")
sys.stdout.write(os.environ["SHIM_STUB_ANSWER"])
sys.exit(int(os.environ["SHIM_STUB_EXIT"]))
"""


class Invocation(TypedDict):
    """One exec the stand-in recorded: its arguments, and its stdin as hex."""

    argv: list[str]
    stdin: str


class Mirror(NamedTuple):
    """A checkout holding the real adapter, whose locked engine is the recording stand-in."""

    shim: Path
    log: Path

    def invocations(self) -> list[Invocation]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def mirror(tmp_path: Path) -> Mirror:
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shim = checkout / "scripts" / SHIM.name
    shutil.copy2(SHIM, shim)
    engine = checkout / ".venv" / "bin" / "onepipeline"
    engine.parent.mkdir(parents=True)
    # llmlint: ignore[e2e_not_mocked] The engine the adapter execs is the published CLI below this repository's wrapper, and what is under test is exactly what reaches it — argv, stdin — and that its answer comes back unchanged, which only a recording stand-in at the adapter's own locked path can observe. The real engine is driven by the last two journeys here and by tests/ask_seam/.  # noqa: E501 - a directive is one line
    engine.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    engine.chmod(engine.stat().st_mode | stat.S_IXUSR)
    return Mirror(shim, tmp_path / "stub.log")


def _through(
    shim: Path,
    arguments: list[str],
    environment: dict[str, str],
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run the adapter exactly as an agent runs `"$ORCHESTRATOR_ASK_MANAGER"`."""
    return subprocess.run(
        [str(shim), *arguments],
        env=environment,
        input=stdin,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _stub_environment(mirror: Mirror, exit_status: int) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "SHIM_STUB_LOG": str(mirror.log),
        "SHIM_STUB_ANSWER": STUB_ANSWER,
        "SHIM_STUB_EXIT": str(exit_status),
    }


def test_every_argument_reaches_the_verb_in_order_and_stdin_unread(mirror: Mirror) -> None:
    """One exec of `onepipeline ask`, with the caller's argv and stdin exactly as given."""
    asked = _through(mirror.shim, ARGUMENTS, _stub_environment(mirror, 0), RAW_STDIN)

    assert asked.returncode == 0, asked.stderr.decode()
    assert mirror.invocations() == [{"argv": ["ask", *ARGUMENTS], "stdin": RAW_STDIN.hex()}]


@pytest.mark.parametrize("exit_status", [0, 1, 2, 7])
def test_the_verbs_answer_and_exit_status_are_the_adapters_own(
    mirror: Mirror, exit_status: int
) -> None:
    asked = _through(mirror.shim, ["q"], _stub_environment(mirror, exit_status), b"")

    assert asked.returncode == exit_status
    assert asked.stdout == STUB_ANSWER.encode()
    assert len(mirror.invocations()) == 1, "the adapter ran the verb more than once"


#: Every execute bit, so clearing them leaves a file that exists and cannot be run.
EXECUTE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def _no_locked_engine(checkout: Path) -> None:
    """Nothing at the locked path at all: a checkout that never installed one."""


def _locked_engine_that_cannot_be_run(checkout: Path) -> None:
    """A locked engine that exists with no execute bit: a half-finished install."""
    engine = checkout / ".venv" / "bin" / "onepipeline"
    engine.parent.mkdir(parents=True)
    # Same stand-in as the mirror's, so what stops it running is the mode and nothing else.
    engine.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    engine.chmod(engine.stat().st_mode & ~EXECUTE_BITS)


@pytest.mark.parametrize(
    "locked_engine",
    [_no_locked_engine, _locked_engine_that_cannot_be_run],
    ids=("absent", "not-executable"),
)
def test_a_checkout_whose_locked_engine_cannot_run_asks_the_one_on_path(
    tmp_path: Path, locked_engine: Callable[[Path], None]
) -> None:
    """Where the checkout's locked engine cannot be run, the adapter runs `PATH`'s.

    The adapter's `[ -x ]` is one test with two ways to be false, and both reach a
    dispatch: a checkout that installed nothing, and one whose install left a file
    behind without its execute bit. Both are driven, because the second is the one that
    would keep passing if the test became `[ -e ]` — the adapter would then exec a file
    it cannot run, and the ask would die at 126 having reached no engine at all.

    The stand-in is put on `PATH` rather than at the checkout's `.venv/bin`, so the only
    way it records an invocation is by the fallback.
    """
    checkout = tmp_path / "bare-checkout"
    (checkout / "scripts").mkdir(parents=True)
    shim = checkout / "scripts" / SHIM.name
    shutil.copy2(SHIM, shim)
    locked_engine(checkout)
    on_path = tmp_path / "bin"
    on_path.mkdir()
    engine = on_path / "onepipeline"
    # llmlint: ignore[e2e_not_mocked] The stand-in is the engine the fallback resolves, and what is under test is only that the fallback reaches it and hands its answer back; the real engine is driven by the real-channel journeys below.  # noqa: E501 - a directive is one line
    engine.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    engine.chmod(engine.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "stub.log"
    environment = {
        "PATH": f"{on_path}{os.pathsep}{os.environ['PATH']}",
        "SHIM_STUB_LOG": str(log),
        "SHIM_STUB_ANSWER": STUB_ANSWER,
        "SHIM_STUB_EXIT": "0",
    }

    asked = _through(shim, ["q"], environment, b"")

    assert asked.returncode == 0, asked.stderr.decode()
    assert asked.stdout == STUB_ANSWER.encode()
    assert Mirror(shim, log).invocations() == [{"argv": ["ask", "q"], "stdin": ""}]


def _asked_on_a_real_channel(tmp_path: Path, arguments: list[str], stdin: bytes | None) -> str:
    """Ask through the real adapter into the installed engine, and read the frame back."""
    runs = tmp_path / "runs"
    run_root(runs, RUN)
    environment = {**os.environ, "ONEPIPELINE_RUN_ID": RUN, "ONEPIPELINE_RUNS_DIR": str(runs)}
    environment.pop("ONEPIPELINE_CHANNEL_ASKER", None)

    asked = _through(SHIM, ["--timeout", "1", *arguments], environment, stdin)

    # Nobody answers, so the ask ends in the bus's own named `timeout` at exit 1.
    assert asked.returncode == 1, asked.stderr.decode()
    assert json.loads(asked.stdout)["answer"] == "timeout"
    claimed = subprocess.run(
        [str(BUS), "next", "surfaces", "--format", "json", "--config", str(CONFIG)]
        + ["--transport-dir", str(runs / RUN / "channel")],
        capture_output=True,
        text=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert claimed.returncode == 0, claimed.stderr
    document = json.loads(claimed.stdout)
    record = document.get("record", document)
    assert record["kind"] == "planner-question"
    assert record["source"] == "proposal"
    assert record["blocking"] is True
    message = record["message"]
    assert isinstance(message, str)
    return message


def test_a_question_asked_with_file_reads_back_off_the_channel_unaltered(tmp_path: Path) -> None:
    question = tmp_path / "question.txt"
    question.write_text(AWKWARD_QUESTION, encoding="utf-8")

    assert _asked_on_a_real_channel(tmp_path, ["--file", str(question)], None) == AWKWARD_QUESTION


def test_a_question_asked_on_stdin_reads_back_off_the_channel_unaltered(tmp_path: Path) -> None:
    message = _asked_on_a_real_channel(tmp_path, [], AWKWARD_QUESTION.encode("utf-8"))

    assert message == AWKWARD_QUESTION
