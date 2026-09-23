"""`scripts/ask-manager.sh` asks through its own checkout's engine, whatever PATH offers first.

The adapter execs the engine's `onepipeline ask`, and the engine is the one this checkout's
lock installed beside its interpreter — `.venv/bin/onepipeline` — resolved from the
script's location, because the engine links the bus a run's channel is written by and a
different release on the caller's PATH is a different bus. `AGENTS.md` states the same rule
for the plan-store CLI; `tests/e2e/test_ask_manager_shim_e2e.py` holds that the adapter
passes everything through untouched, and what this journey holds is the *resolution*,
against this checkout's real engine.

A stand-in `onepipeline` that records its every invocation is put first on PATH, and the
real adapter — from a directory that is not this checkout, as a worker asks — never runs
it: the question reaches a real channel through this checkout's own engine and is read back
through the bus. The launch record the engine reads its policy from is the builder's in
`tests/e2e/probe_run_root.py`, held to the engine's own declaration by
`tests/test_engine_contracts.py`, so no launch is spent.

llmlint: ignore-file[tests_mirror_real_usage] The run root is the builder's rather than a
launch's because the subject is which engine binary the adapter resolves, which a launch
does not change and would cost a whole run to set up; the builder's records are held to
the installed engine's declarations, and `tests/ask_seam/ask_manager/` asks through the
same adapter on runs `just orchestrate` really launched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from probe_run_root import run_root
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The adapter under test, the configuration the channel is read back under, and the bus
#: that reads it.
ASK_MANAGER = REPO_ROOT / "scripts" / "ask-manager.sh"
CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"
BUS = REPO_ROOT / ".venv" / "bin" / "onemessagebus"

#: The run whose channel is asked on, and the reply window: nobody answers, so waiting it
#: out is the whole of the round trip.
RUN = "engine-resolution-run"
WINDOW_SECONDS = 1
QUESTION = "Which engine answers this question?"

#: What the stand-in on PATH answers with, if it is ever run: a reply at exit 0, so an ask
#: that reached it reads as an answered question rather than as the real engine's timeout.
STAND_IN = """\
#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STAND_IN_LOG"
printf '%s\\n' '{"answer":"reply","correlation":"stand-in"}'
exit 0
"""


def test_the_adapter_asks_through_this_checkouts_own_engine_and_never_the_one_first_on_path(
    tmp_path: Path,
) -> None:
    decoys = tmp_path / "decoys"
    decoys.mkdir()
    log = tmp_path / "stand-in.log"
    stand_in = decoys / "onepipeline"
    # llmlint: ignore[e2e_not_mocked] Nothing the adapter runs is doubled: the stand-in exists to be *not* run, recording any invocation that reaches it, and the ask below goes through the real engine over a real channel.  # noqa: E501 - a directive is one line
    stand_in.write_text(STAND_IN, encoding="utf-8")
    stand_in.chmod(0o755)
    assert shutil.which("onepipeline", path=f"{decoys}{os.pathsep}{os.environ['PATH']}") == str(
        stand_in
    ), "PATH does not resolve onepipeline to the stand-in, so this proves nothing"
    runs = tmp_path / "runs"
    run_root(runs, RUN)
    # Where a worker asks from: a directory holding no `runs`, no `config` and no `.venv`.
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    environment = {
        **os.environ,
        "PATH": f"{decoys}{os.pathsep}{os.environ['PATH']}",
        "STAND_IN_LOG": str(log),
        "ONEPIPELINE_RUN_ID": RUN,
        "ONEPIPELINE_RUNS_DIR": str(runs),
    }
    environment.pop("ONEPIPELINE_CHANNEL_ASKER", None)

    asked = subprocess.run(  # noqa: S603 - the real adapter, as an agent runs it
        [str(ASK_MANAGER), "--timeout", str(WINDOW_SECONDS), QUESTION],
        cwd=worktree,
        env=environment,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert not log.exists(), f"the adapter ran the engine first on PATH: {log.read_text()}"
    assert asked.returncode == 1, asked.stdout + asked.stderr
    assert json.loads(asked.stdout)["answer"] == "timeout", asked.stdout
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
    assert document.get("record", document)["message"] == QUESTION
