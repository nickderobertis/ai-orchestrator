"""The Codex stand-ins treat `codex app-server` as codex does, driven as the processes they are.

Real `codex app-server` refuses an argument it does not accept — `--skip-git-repo-check`,
which only `codex exec` takes, among them — exiting 2 with clap's `error: unexpected argument
'<arg>' found` and starting nothing, and it starts in a directory no repository holds with no
trust argument at all; only an `exec` turn, `exec resume` among them, is refused such a
directory. A stand-in that accepted what codex rejects let a judge config carrying that flag
kill every Codex-supervised dispatch on the host while every journey stayed green, and one that
refused `app-server` outside a repository recorded refusals codex never makes.

So each stand-in a journey can name as Codex's binary is started here exactly as `oneharness`
starts it — the file itself, its argv, its frames on stdin — and what it printed, exited with
and recorded is read back. `tests/e2e/fake_codex.py` holds the accepted options and the codex
release they were read from; the journey proving a real dispatch's supervisor meets them is
`tests/e2e/launch_monitor_observer/test_codex_supervised_dispatch_e2e.py`.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Not expensive: each case
starts one stdlib Python process and no engine, harness or provider, in well under a second.

llmlint: ignore-file[shell_test_tiers_stay_split] Pytest cases over the stand-in processes,
not a shell suite, at the cost above.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

import pytest
from fake_codex import (
    APP_SERVER_ENV,
    APP_SERVER_REFUSAL_LOG_ENV,
    APP_SERVER_THREAD,
    MODELS_CACHE,
    InputItem,
)
from fake_codex_untrusted_directory import REFUSAL
from project_fixtures import helper
from waits import timeout as e2e_timeout

#: Every stand-in a journey names as Codex's binary for `app-server` launches.
STAND_INS = {
    "fake_codex": helper("fake_codex.py"),
    "fake_codex_untrusted_directory": helper("fake_codex_untrusted_directory.py"),
}

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] What codex-cli 0.159.2 printed, measured once and pinned with `tests/e2e/fake_codex.py`'s `APP_SERVER_RELEASE`; by the manager's ruling (c3.0) no suite gate runs the host's installed codex, and the wording is re-taken by hand on the codex bump that moves that release.  # noqa: E501 - a directive is one line
#: The usage block clap prints under each `codex app-server` refusal, measured from
#: codex-cli 0.159.2's own output.
USAGE = "\nUsage: codex app-server [OPTIONS] [COMMAND]\n\nFor more information, try '--help'.\n"
#: The same block once the launch has used `--config`: clap names each option already used.
USED_CONFIG_USAGE = (
    "\nUsage: codex app-server --config <key=value>\n\nFor more information, try '--help'.\n"
)
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]

#: The argument a judge config once carried into every supervisor's `codex app-server`.
EXEC_ONLY_FLAG = "--skip-git-repo-check"

#: What a served turn answers, scripted so the answer read back is this one's.
ANSWER = "served on app-server"
PROMPT = "Say what you saw."


def _outside_every_repository(tmp_path: Path) -> Path:
    directory = tmp_path / "scratch"
    directory.mkdir(parents=True)
    probed = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=directory,
        text=True,
        capture_output=True,
        check=False,
    )
    assert probed.returncode != 0, f"{directory} is inside a work tree"
    return directory


class Records:
    """Every file a launch could write to, so a journey reads what it did."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "codex-home"
        self.home.mkdir()
        self.attempts = tmp_path / "attempts.log"
        self.prompts = tmp_path / "prompts.jsonl"
        self.refusals = tmp_path / "refusals.jsonl"

    def environment(self) -> dict[str, str]:
        inherited = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("ONEHARNESS_", "FAKE_CODEX_", "CODEX_"))
        }
        return {
            **inherited,
            "CODEX_HOME": str(self.home),
            APP_SERVER_ENV: "1",
            APP_SERVER_REFUSAL_LOG_ENV: str(self.refusals),
            "FAKE_CODEX_ATTEMPT_LOG": str(self.attempts),
            "FAKE_CODEX_PROMPT_LOG": str(self.prompts),
            "FAKE_CODEX_ANSWERS": json.dumps([ANSWER]),
        }


class ClientFrame(TypedDict):
    """One frame a client sends `codex app-server`: a request, or with no `id` a notification."""

    jsonrpc: Literal["2.0"]
    id: NotRequired[int]
    method: str
    params: dict[str, object]


#: What `oneharness` sends a per-dispatch `codex app-server` to open one turn.
OPENING = [
    ClientFrame(jsonrpc="2.0", id=1, method="initialize", params={"clientInfo": {}}),
    ClientFrame(jsonrpc="2.0", method="initialized", params={}),
    ClientFrame(jsonrpc="2.0", id=2, method="thread/start", params={"approvalPolicy": "never"}),
    ClientFrame(
        jsonrpc="2.0",
        id=3,
        method="turn/start",
        params={
            "threadId": APP_SERVER_THREAD,
            "input": [InputItem(type="text", text=PROMPT)],
        },
    ),
]


def _launch(
    stand_in: Path, argv: list[str], where: Path, records: Records
) -> subprocess.CompletedProcess[str]:
    """Start the stand-in as Codex, hand it one turn's frames, and close its stdin."""
    return subprocess.run(  # noqa: S603 - the stand-in, as oneharness starts Codex
        [str(stand_in), *argv],
        cwd=where,
        env=records.environment(),
        input="".join(json.dumps(frame) + "\n" for frame in OPENING),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )


@pytest.mark.parametrize("stand_in", STAND_INS.values(), ids=STAND_INS.keys())
@pytest.mark.parametrize(
    ("argv", "unexpected", "usage"),
    [
        ([EXEC_ONLY_FLAG], EXEC_ONLY_FLAG, USAGE),
        (["-c", "features.apps=false", EXEC_ONLY_FLAG], EXEC_ONLY_FLAG, USED_CONFIG_USAGE),
        (["--sandbox", "read-only"], "--sandbox", USAGE),
        (["--bogus=1"], "--bogus", USAGE),
    ],
    ids=["exec-only-flag", "after-a-config", "exec-sandbox", "attached-value"],
)
def test_an_argument_codex_app_server_rejects_is_refused_and_nothing_starts(
    tmp_path: Path, stand_in: Path, argv: list[str], unexpected: str, usage: str
) -> None:
    records = Records(tmp_path)

    refused = _launch(stand_in, ["app-server", *argv], _outside_every_repository(tmp_path), records)

    assert refused.returncode == 2, refused
    assert refused.stderr == f"error: unexpected argument '{unexpected}' found\n{usage}"
    assert refused.stdout == "", "a refused app-server answered a frame"
    for written in (records.attempts, records.prompts, records.home / MODELS_CACHE):
        assert not written.exists(), f"a refused app-server started a turn: {written}"
    (logged,) = [json.loads(line) for line in records.refusals.read_text("utf-8").splitlines()]
    assert logged == {"argv": ["app-server", *argv], "stderr": refused.stderr.rstrip("\n")}


@pytest.mark.parametrize("stand_in", STAND_INS.values(), ids=STAND_INS.keys())
def test_an_option_missing_its_value_is_refused(tmp_path: Path, stand_in: Path) -> None:
    records = Records(tmp_path)

    refused = _launch(stand_in, ["app-server", "-c"], _outside_every_repository(tmp_path), records)

    assert refused.returncode == 2, refused
    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] What codex-cli 0.159.2 printed, measured once and pinned with `tests/e2e/fake_codex.py`'s `APP_SERVER_RELEASE`; by the manager's ruling (c3.0) no suite gate runs the host's installed codex, and the wording is re-taken by hand on the codex bump that moves that release.  # noqa: E501 - a directive is one line
    assert refused.stderr == (
        "error: a value is required for '--config <key=value>' but none was supplied\n\n"
        "For more information, try '--help'.\n"
    )
    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
    assert refused.stdout == ""


@pytest.mark.parametrize("stand_in", STAND_INS.values(), ids=STAND_INS.keys())
def test_app_server_starts_outside_every_repository_and_serves_a_turn(
    tmp_path: Path, stand_in: Path
) -> None:
    """No trust argument, no repository, and the turn is answered over the protocol."""
    records = Records(tmp_path)
    argv = ["app-server", "-c", "features.apps=false", "-c", "analytics.enabled=false"]

    served = _launch(stand_in, argv, _outside_every_repository(tmp_path), records)

    assert served.returncode == 0, served
    frames = [json.loads(line) for line in served.stdout.splitlines()]
    answered = {frame["id"]: frame for frame in frames if "id" in frame}
    assert set(answered) == {1, 2, 3}, frames
    assert all("result" in frame for frame in answered.values()), frames
    assert answered[2]["result"]["thread"]["id"] == APP_SERVER_THREAD
    said = [
        frame["params"]["item"]["text"]
        for frame in frames
        if frame.get("method") == "item/completed"
    ]
    assert said == [ANSWER], frames
    (ended,) = [frame for frame in frames if frame.get("method") == "turn/completed"]
    assert ended["params"]["turn"]["status"] == "completed", ended
    (turn,) = [json.loads(line) for line in records.prompts.read_text("utf-8").splitlines()]
    assert turn["prompt"] == PROMPT
    assert turn["argv"] == argv
    assert (records.home / MODELS_CACHE).is_file(), "no model catalog was written on start"
    assert not records.refusals.exists()


@pytest.mark.parametrize(
    "argv",
    [["exec", "--json", PROMPT], ["exec", "resume", "a-thread", "--json", PROMPT]],
    ids=["exec", "exec-resume"],
)
def test_an_exec_turn_outside_every_repository_is_still_refused_without_a_trust_argument(
    tmp_path: Path, argv: list[str]
) -> None:
    records = Records(tmp_path)
    where = _outside_every_repository(tmp_path)
    stand_in = STAND_INS["fake_codex_untrusted_directory"]

    refused = _launch(stand_in, argv, where, records)
    trusted = _launch(stand_in, [*argv, EXEC_ONLY_FLAG], where, records)

    assert refused.returncode == 1, refused
    assert REFUSAL in refused.stderr, refused.stderr
    assert trusted.returncode == 0, trusted
    assert REFUSAL not in trusted.stderr, trusted.stderr
