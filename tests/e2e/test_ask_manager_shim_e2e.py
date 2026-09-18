"""`scripts/ask-manager.sh` turns its three invocation forms into one `onemessagebus ask`.

The shim is what `ORCHESTRATOR_ASK_MANAGER` names, and every task the dispatch appendix
reaches spells its three forms: the question as one argument, `--file <path>`, and stdin.
Everything after the question is on the bus — the correlation, the wait, the answer — is
the installed `onemessagebus` release's, so what this module holds is the translation and
nothing else: one exec per ask, with the argv this host's configuration and the run's own
channel directory make, the question as the frame on stdin, and the bus's answer and exit
status passed through untouched.

The published CLI is doubled by a recording stub placed first on a PATH that holds nothing
else a shell could spawn, so a second process — a retry, a re-ask, a queue read — fails
the journey rather than passing silently. The last journey drives the real bus instead,
because what an escaped question reads back as is the bus's to say.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_recipes

SHIM = REPO_ROOT / "scripts" / "ask-manager.sh"
CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"

RUN = "shim-run-1"
ASKER = "worker-7"

#: The argv every ask begins with, before the run's channel directory, the asker and the
#: shim's own appendages: the reply window as `--timeout`, and `--about` only when a node
#: is named.
PREFIX_WITHOUT_CHANNEL = ["ask", "surfaces", "--blocking", "--config", str(CONFIG)]

#: The shim's reply window when none is named, which is `config/onemessagebus.yaml`'s
#: `codecs.monitor.reply_window_seconds` (`tests/test_onemessagebus_config.py`).
DEFAULT_WINDOW = "3000"

REPLY_ANSWER = (
    '{"answer":"reply","correlation":"c-1","reply":{"id":0,'
    '"reply":{"version":3,"completion":true,"message":"main"},"correlation":"c-1"}}\n'
)
TIMEOUT_ANSWER = '{"answer":"timeout","correlation":"c-1"}\n'

#: A question carrying every byte JSON reserves, plus a control and non-ASCII text.
AWKWARD_QUESTION = 'back\\slash "quoted"\nsecond line\r\ttabbed \x01 ünï 日本'

STUB = """\
#!{python}
import json, os, sys
record = {{"argv": sys.argv[1:], "stdin": sys.stdin.buffer.read().decode("utf-8", "replace")}}
with open(os.environ["SHIM_STUB_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(record) + "\\n")
sys.stdout.write(os.environ["SHIM_STUB_ANSWER"])
sys.stderr.write("correlation: c-1\\n")
sys.exit(int(os.environ["SHIM_STUB_EXIT"]))
"""


class Invocation(TypedDict):
    """One exec the stub recorded: the arguments it was handed and the whole of its stdin."""

    argv: list[str]
    stdin: str


class Bench(NamedTuple):
    """A PATH holding only the stub and the two programs the shebang needs."""

    bin: Path
    log: Path
    runs: Path

    def environment(self, answer: str = REPLY_ANSWER, exit_status: int = 0) -> dict[str, str]:
        """The environment a dispatch asks from, pointed at the stub."""
        return {
            "PATH": str(self.bin),
            "HOME": os.environ.get("HOME", "/"),
            "LC_ALL": "C.UTF-8",
            "ONEPIPELINE_RUN_ID": RUN,
            "ONEPIPELINE_RUNS_DIR": str(self.runs),
            "ONEPIPELINE_CHANNEL_ASKER": ASKER,
            "SHIM_STUB_LOG": str(self.log),
            "SHIM_STUB_ANSWER": answer,
            "SHIM_STUB_EXIT": str(exit_status),
        }

    def invocations(self) -> list[Invocation]:
        """Every exec the stub recorded, oldest first."""
        if not self.log.exists():
            return []
        # `cast` rather than a validating read: `STUB` above writes exactly this shape.
        return [
            cast(Invocation, json.loads(line)) for line in self.log.read_text("utf-8").splitlines()
        ]


@pytest.fixture
def bench(tmp_path: Path) -> Iterator[Bench]:
    """A stub `onemessagebus`, and a run root whose channel directory nobody may read."""
    directory = tmp_path / "bin"
    directory.mkdir()
    stub = directory / "onemessagebus"
    # llmlint: ignore[e2e_not_mocked] The published CLI the shim execs is the one double
    # tests/AGENTS.md sanctions below a wrapper: what is under test is the argv and stdin the
    # shim hands it and that nothing else is spawned, which only a recording stand-in on an
    # otherwise empty PATH can observe. The real bus is driven by the last journey here and
    # by tests/ask_seam/test_ask_manager_e2e.py.
    stub.write_text(STUB.format(python=sys.executable), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    for program in ("env", "bash"):
        found = shutil.which(program)
        assert found is not None, f"{program} is not on this host's PATH"
        (directory / program).symlink_to(found)
    runs = tmp_path / "runs"
    channel = runs / RUN / "channel"
    channel.mkdir(parents=True)
    channel.chmod(0)
    try:
        yield Bench(directory, tmp_path / "stub.log", runs)
    finally:
        channel.chmod(stat.S_IRWXU)


def _ask(
    arguments: list[str], environment: dict[str, str], stdin: str | bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run the shim exactly as an agent runs `"$ORCHESTRATOR_ASK_MANAGER"`."""
    return subprocess.run(
        [str(SHIM), *arguments],
        env=environment,
        input=stdin.encode("utf-8") if isinstance(stdin, str) else stdin,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )


def _expected_argv(bench: Bench) -> list[str]:
    return [
        *PREFIX_WITHOUT_CHANNEL,
        "--transport-dir",
        str(bench.runs / RUN / "channel"),
        "--asker",
        ASKER,
        "--timeout",
        DEFAULT_WINDOW,
    ]


def _frame(question: str) -> str:
    return json.dumps(
        {"kind": "planner-question", "message": question, "source": "proposal"},
        ensure_ascii=False,
        separators=(",", ":"),
    )


@pytest.mark.parametrize("form", ["argument", "file", "stdin"])
def test_each_invocation_form_is_one_exec_of_the_bus_ask(
    bench: Bench, tmp_path: Path, form: str
) -> None:
    """The argv is `ask`'s over the run's channel plus `--timeout`, and stdin is the frame alone.

    `ask surfaces --blocking --config <path> --transport-dir <dir> --asker <asker>`, then
    `--timeout <window>` — the shim's one appendage when no node is named, because `ask`
    reads the reply window from no configuration, and without it a question nobody answers
    waits for as long as the dispatch runs.
    """
    question = "Should the cursor be an opaque token or a node id?"
    match form:
        case "argument":
            result = _ask([question], bench.environment())
        case "file":
            written = tmp_path / "question.md"
            written.write_text(question, encoding="utf-8")
            result = _ask(["--file", str(written)], bench.environment())
        case _:
            result = _ask([], bench.environment(), stdin=question)

    assert result.returncode == 0, result.stderr.decode()
    invocations = bench.invocations()
    assert len(invocations) == 1, invocations
    assert invocations[0]["argv"] == _expected_argv(bench)
    assert invocations[0]["stdin"] == _frame(question) + "\n"
    assert json.loads(invocations[0]["stdin"])["message"] == question


def test_a_named_node_is_the_one_further_appendage(bench: Bench) -> None:
    """`--about <node>` follows `--timeout` exactly when the node variable is set."""
    environment = {**bench.environment(), "ORCHESTRATOR_ASK_MANAGER_NODE": "node-a"}
    environment["ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS"] = "45"
    result = _ask(["Which base?"], environment)

    assert result.returncode == 0, result.stderr.decode()
    [invocation] = bench.invocations()
    expected = _expected_argv(bench)
    expected[-1] = "45"
    assert invocation["argv"] == [*expected, "--about", "node-a"]


@pytest.mark.parametrize(
    ("variable", "value", "named"),
    [
        ("ONEPIPELINE_CHANNEL_ASKER", "   ", b"ONEPIPELINE_CHANNEL_ASKER is blank"),
        (
            "ORCHESTRATOR_ASK_MANAGER_NODE",
            "node-a\nnode-b",
            b"ORCHESTRATOR_ASK_MANAGER_NODE is not",
        ),
        ("ORCHESTRATOR_ASK_MANAGER_NODE", "n" * 513, b"ORCHESTRATOR_ASK_MANAGER_NODE is not"),
    ],
    ids=["a blank asker", "a two-line node", "a node past 512 bytes"],
)
def test_an_asker_or_node_the_bus_would_refuse_is_refused_by_name(
    bench: Bench, variable: str, value: str, named: bytes
) -> None:
    """What the bus would refuse as `--asker` or `--about` is refused here, naming its variable."""
    result = _ask(["Which base?"], {**bench.environment(), variable: value})

    assert result.returncode == 2, result.stderr.decode()
    assert named in result.stderr, result.stderr.decode()
    assert bench.invocations() == []


def test_a_node_of_exactly_512_bytes_is_still_asked_about(bench: Bench) -> None:
    """The bound is the bus's own, so a node at it is forwarded as it was named."""
    node = "n" * 512
    result = _ask(["Which base?"], {**bench.environment(), "ORCHESTRATOR_ASK_MANAGER_NODE": node})

    assert result.returncode == 0, result.stderr.decode()
    [invocation] = bench.invocations()
    assert invocation["argv"][-2:] == ["--about", node]


class Edge(NamedTuple):
    """One value an ask's environment could carry, at or past an edge of the bus's rule."""

    variable: str
    flag: str
    value: str


#: Each side of every edge of the installed bus's rules for `--about` and `--asker`: its
#: byte bound, the control characters it refuses, and a blank value.
EDGES = (
    Edge("ORCHESTRATOR_ASK_MANAGER_NODE", "--about", "n" * 512),
    Edge("ORCHESTRATOR_ASK_MANAGER_NODE", "--about", "n" * 513),
    Edge("ORCHESTRATOR_ASK_MANAGER_NODE", "--about", "node\nnext"),
    Edge("ORCHESTRATOR_ASK_MANAGER_NODE", "--about", "node\rnext"),
    Edge("ORCHESTRATOR_ASK_MANAGER_NODE", "--about", "node\tnext"),
    Edge("ORCHESTRATOR_ASK_MANAGER_NODE", "--about", "node\x7fnext"),
    Edge("ORCHESTRATOR_ASK_MANAGER_NODE", "--about", "   "),
    Edge("ONEPIPELINE_CHANNEL_ASKER", "--asker", "   "),
    Edge("ONEPIPELINE_CHANNEL_ASKER", "--asker", "worker\nnext"),
    Edge("ONEPIPELINE_CHANNEL_ASKER", "--asker", "w" * 513),
)


@pytest.mark.parametrize(
    "edge",
    EDGES,
    ids=[
        "node of 512 bytes",
        "node of 513 bytes",
        "node with a line break",
        "node with a carriage return",
        "node with a tab",
        "node with a delete",
        "blank node",
        "blank asker",
        "asker with a line break",
        "asker of 513 bytes",
    ],
)
def test_the_shim_refuses_exactly_what_the_installed_bus_refuses(
    bench: Bench, tmp_path: Path, edge: Edge
) -> None:
    """The shim's checks on the asker and the node are the bus's, held to the bus itself.

    The shim refuses a value before it can reach `onemessagebus ask`, so its rule is a
    restatement of the bus's, and a restatement drifts. So each value is put to both: the
    shim against the recording stub, and the installed bus as the same flag on a real ask
    that nobody answers. A refusal from either is exit 2; an accepted value is asked, so
    the stub answers 0 and the real ask ends in its one-second `timeout` at exit 1. The two
    must agree on every edge.
    """
    shim = _ask(["Which base?"], {**bench.environment(), edge.variable: edge.value})
    assert shim.returncode in (0, 2), shim.stderr.decode()

    channel = tmp_path / "bus-channel"
    channel.mkdir()
    named_asker = [] if edge.flag == "--asker" else ["--asker", ASKER]
    bus = subprocess.run(
        [
            "onemessagebus",
            "ask",
            "surfaces",
            "--blocking",
            "--timeout",
            "1",
            "--config",
            str(CONFIG),
            "--transport-dir",
            str(channel),
            *named_asker,
            edge.flag,
            edge.value,
        ],
        input=_frame("Which base?"),
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert bus.returncode in (1, 2), bus.stderr

    assert (shim.returncode == 2) == (bus.returncode == 2), (
        f"for {edge.flag} {edge.value!r} the shim exited {shim.returncode} and the installed "
        f"bus exited {bus.returncode}:\nshim: {shim.stderr.decode()}\nbus: {bus.stderr}"
    )


@pytest.mark.parametrize(
    ("answer", "exit_status"), [(REPLY_ANSWER, 0), (TIMEOUT_ANSWER, 1)], ids=["reply", "timeout"]
)
def test_the_buses_answer_and_exit_status_pass_through_untouched(
    bench: Bench, answer: str, exit_status: int
) -> None:
    """Whatever the bus answers is the shim's whole stdout and its exit, asked once."""
    result = _ask(["Which base?"], bench.environment(answer, exit_status))

    assert result.stdout.decode("utf-8") == answer
    assert result.returncode == exit_status
    assert len(bench.invocations()) == 1, "the shim asked more than once"


def test_the_question_is_escaped_into_the_frame_byte_for_byte(bench: Bench) -> None:
    """Backslash, quote, newline, carriage return, tab and a control survive the frame."""
    result = _ask([AWKWARD_QUESTION], bench.environment())

    assert result.returncode == 0, result.stderr.decode()
    [invocation] = bench.invocations()
    assert json.loads(invocation["stdin"])["message"] == AWKWARD_QUESTION


def test_a_question_that_is_not_utf8_is_refused_and_nothing_is_asked(bench: Bench) -> None:
    """A malformed frame is never sent: the shim names why and the bus is not spawned."""
    result = _ask([], bench.environment(), stdin=b"which base? \xff\xfe")

    assert result.returncode == 2
    assert b"not well-formed UTF-8" in result.stderr
    assert bench.invocations() == []


@pytest.mark.parametrize("form", ["file", "stdin"])
def test_a_question_carrying_a_nul_byte_is_refused_rather_than_cut_short(
    bench: Bench, tmp_path: Path, form: str
) -> None:
    """A NUL ends a shell read early, so a question carrying one is refused, never truncated."""
    question = b"which base?\x00and the rest"
    if form == "file":
        written = tmp_path / "question.md"
        written.write_bytes(question)
        result = _ask(["--file", str(written)], bench.environment())
    else:
        result = _ask([], bench.environment(), stdin=question)

    assert result.returncode == 2, result.stderr.decode()
    assert b"carries a NUL byte" in result.stderr
    assert bench.invocations() == []


@pytest.mark.parametrize("form", ["file", "stdin"])
def test_a_question_that_cannot_be_read_is_refused_by_name_and_nothing_is_asked(
    bench: Bench, tmp_path: Path, form: str
) -> None:
    """Input that opens but fails to read — a directory — is named, never a shell error.

    bash's `read` answers a failed read with the status it gives the end of input, so a shim
    reading that status alone would go on to ask with no question at all.
    """
    unreadable = tmp_path / "question.md"
    unreadable.mkdir()
    if form == "file":
        result = _ask(["--file", str(unreadable)], bench.environment())
    else:
        # A descriptor rather than `open`, which refuses a directory, where a shell's `<`
        # opens one read-only exactly as this does.
        directory = os.open(unreadable, os.O_RDONLY)
        try:
            result = subprocess.run(
                [str(SHIM)],
                env=bench.environment(),
                stdin=directory,
                capture_output=True,
                timeout=e2e_timeout(30),
                check=False,
            )
        finally:
            os.close(directory)

    assert result.returncode == 2, result.stderr.decode()
    assert b"could not be read (read exited 1)" in result.stderr, result.stderr
    assert b"unbound variable" not in result.stderr, result.stderr
    assert result.stdout == b"", "a refusal printed something an asker could read as an answer"
    assert bench.invocations() == []


def test_a_question_file_that_cannot_be_opened_is_refused_by_name(
    bench: Bench, tmp_path: Path
) -> None:
    """A path `-r` passes but no read can open — a unix socket — is named, and nothing asked."""
    path = tmp_path / "question.sock"
    listener = socket.socket(socket.AF_UNIX)
    try:
        listener.bind(str(path))
        assert os.access(path, os.R_OK), "the socket is not readable, so this proves nothing"
        result = _ask(["--file", str(path)], bench.environment())
    finally:
        listener.close()

    assert result.returncode == 2, result.stderr.decode()
    assert f"the question file '{path}' could not be opened".encode() in result.stderr
    assert result.stdout == b""
    assert bench.invocations() == []


def test_an_unset_run_is_refused_by_name_and_nothing_is_asked(bench: Bench) -> None:
    """With no run there is no channel directory to name, so nothing is guessed."""
    environment = bench.environment()
    del environment["ONEPIPELINE_RUN_ID"]
    result = _ask(["Which base?"], environment)

    assert result.returncode == 2
    assert b"ONEPIPELINE_RUN_ID is not set" in result.stderr
    assert bench.invocations() == []


def test_a_path_without_the_bus_is_refused_by_name_with_the_way_back(bench: Bench) -> None:
    """With no `onemessagebus` to exec, the shim says so and what restores it, not a shell error."""
    (bench.bin / "onemessagebus").unlink()
    result = _ask(["Which base?"], bench.environment())

    assert result.returncode == 2, result.stderr
    assert b"onemessagebus is not on PATH" in result.stderr, result.stderr
    assert b"just bootstrap" in result.stderr, result.stderr
    assert result.stdout == b"", "a refusal printed something an asker could read as an answer"


def test_an_escaped_question_reads_back_from_a_real_channel_as_it_was_asked(
    tmp_path: Path,
) -> None:
    """Through the installed bus: the queued surface's `message` is the question, exactly.

    Nobody answers, so the ask ends in the bus's own named `timeout` at a non-zero exit —
    which is also the shim passing a real timeout through.
    """
    runs = tmp_path / "runs"
    channel = runs / RUN / "channel"
    channel.mkdir(parents=True)
    environment = {
        **os.environ,
        "ONEPIPELINE_RUN_ID": RUN,
        "ONEPIPELINE_RUNS_DIR": str(runs),
        "ONEPIPELINE_CHANNEL_ASKER": ASKER,
        "ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS": "1",
    }
    environment.pop("ORCHESTRATOR_ASK_MANAGER_NODE", None)
    asked = _ask([AWKWARD_QUESTION], environment)

    assert asked.returncode == 1, asked.stderr.decode()
    assert json.loads(asked.stdout)["answer"] == "timeout"
    claimed = subprocess.run(
        [
            "onemessagebus",
            "next",
            "surfaces",
            "--format",
            "json",
            "--config",
            str(CONFIG),
            "--transport-dir",
            str(channel),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert claimed.returncode == 0, claimed.stderr
    document = json.loads(claimed.stdout)
    record = document.get("record", document)
    assert record["message"] == AWKWARD_QUESTION
    assert record["asker"] == ASKER
    assert record["blocking"] is True
