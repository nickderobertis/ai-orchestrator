"""`scripts/ask-manager.sh` asks through its own checkout's bus, whatever the caller's PATH offers.

The shim reads `config/onemessagebus.yaml` from its own checkout, resolved from the
script's location, and the bus it hands that configuration to has to be the same
checkout's — `.venv/bin/onemessagebus`, installed from the project lock beside the
interpreter — because a configuration and a bus of different releases are read against each
other and refuse. A bus the caller's PATH offers can be of another release, and a refused ask
leaves a dispatched agent with no way to reach its manager. `AGENTS.md` states the same rule
for the plan-store CLI, and `tests/e2e/test_ask_manager_shim_e2e.py` holds the translation the shim
performs; what this journey holds is the *resolution*, against this checkout's real bus.

One property each. A stand-in `onemessagebus` that records its every invocation is put
first on PATH, and the real shim — from a directory that is not this checkout, as a worker
asks — never runs it: the question reaches a real channel through this checkout's own
`.venv/bin/onemessagebus`, read back through that same bus. And a checkout with no bus it
can run — absent, a directory of that name, or a file with no execute bit — is refused
before anything is asked, naming that path and the step that installs it, with the
stand-in still first on PATH and still never run: falling back to the search path would be
the mismatch this resolution exists to prevent.

Neither spends a launch: the shim names a run's channel directory and never reads the run,
so a directory under the runs root is a channel enough for the bus to queue on.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The shim under test, run as an agent runs it, and the two things it resolves from its own
#: location: the configuration every ask is made under, and the bus that ask is made with.
ASK_MANAGER = REPO_ROOT / "scripts" / "ask-manager.sh"
CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"
BUS = REPO_ROOT / ".venv" / "bin" / "onemessagebus"

#: The run whose channel is asked on, who asks, and the reply window: nobody answers, so
#: waiting it out is the whole of the round trip.
RUN = "bus-resolution-run"
ASKER = "bus-resolution-worker"
WINDOW_SECONDS = 1
QUESTION = "Which bus answers this question?"

#: What the stand-in on PATH answers with, if it is ever run: a line the shim would pass
#: through as the bus's answer, at the bus's own exit for a reply, so an ask that reached it
#: reads as an answered question rather than a stand-in.
STAND_IN_ANSWER = '{"answer":"reply","correlation":"stand-in","reply":{"reply":"never"}}'
STAND_IN = """\
#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$STAND_IN_LOG"
printf '%s\\n' '{answer}'
exit 0
"""


class Bench(NamedTuple):
    """A PATH led by the recording stand-in, a runs root, and where the shim is asked from."""

    environment: dict[str, str]
    log: Path
    channel: Path
    cwd: Path


def _bench(tmp_path: Path) -> Bench:
    """The stand-in first on PATH, a channel directory to queue on, and a foreign directory.

    The rest of PATH is kept, so the real bus is *also* reachable by name behind the
    stand-in: a shim that consulted the search path at all would find the stand-in first,
    and one that skipped past it would still be resolving somewhere other than its own
    checkout.
    """
    decoys = tmp_path / "decoys"
    decoys.mkdir()
    log = tmp_path / "stand-in.log"
    stand_in = decoys / "onemessagebus"
    # llmlint: ignore[e2e_not_mocked] Nothing the shim runs is doubled: the stand-in exists to be *not* run, recording any invocation that reaches it, and the ask below goes through the real bus over the real configuration.  # noqa: E501 - a directive is one line, and its reason is longer than the limit
    stand_in.write_text(STAND_IN.format(answer=STAND_IN_ANSWER), encoding="utf-8")
    stand_in.chmod(0o755)
    runs = tmp_path / "runs"
    channel = runs / RUN / "channel"
    channel.mkdir(parents=True)
    # Where a worker asks from: a directory holding no `runs`, no `config` and no `.venv`,
    # so a shim resolving any of the three against its working directory finds nothing.
    cwd = tmp_path / "worktree"
    cwd.mkdir()
    # The enclosing dispatch's environment with this journey's own run, asker and window
    # written over it, exactly as `tests/e2e/test_ask_manager_shim_e2e.py` asks a real bus:
    # what the dispatch names is asked on its own channel, and `--about` is the one
    # appendage that cannot be stated as a value, so an inherited node is dropped instead.
    environment = {
        **os.environ,
        "PATH": os.pathsep.join((str(decoys), os.environ["PATH"])),
        "STAND_IN_LOG": str(log),
        "ONEPIPELINE_RUN_ID": RUN,
        "ONEPIPELINE_RUNS_DIR": str(runs),
        "ONEPIPELINE_CHANNEL_ASKER": ASKER,
        "ORCHESTRATOR_ASK_MANAGER_TIMEOUT_SECONDS": str(WINDOW_SECONDS),
    }
    environment.pop("ORCHESTRATOR_ASK_MANAGER_NODE", None)
    found = shutil.which("onemessagebus", path=environment["PATH"])
    assert found == str(stand_in), (
        f"PATH resolves onemessagebus to {found}, not to the stand-in, so nothing here can say "
        "whether the shim consulted it"
    )
    return Bench(environment, log, channel, cwd)


def _ask(shim: Path, bench: Bench) -> subprocess.CompletedProcess[str]:
    """Run ``shim`` exactly as an agent runs `"$ORCHESTRATOR_ASK_MANAGER"`, from the worktree."""
    return subprocess.run(  # noqa: S603 - the real shim, as an agent runs it
        [str(shim), QUESTION],
        cwd=bench.cwd,
        env=bench.environment,
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(60),
        check=False,
    )


def _queued(bench: Bench) -> list[dict[str, object]]:
    """Every record standing on the run's `surfaces` queue, oldest first.

    Read through this checkout's own bus, named by its path rather than left to PATH, which
    the stand-in leads. `next` hands out one record per call, so the queue is drained to the
    end: a journey that read only the first could not tell one question from two.
    """
    records: list[dict[str, object]] = []
    while True:
        listed = subprocess.run(  # noqa: S603 - this checkout's installed bus, reading a queue
            [
                str(BUS),
                "next",
                "surfaces",
                "--format",
                "json",
                "--config",
                str(CONFIG),
                "--transport-dir",
                str(bench.channel),
            ],
            cwd=bench.cwd,
            env=bench.environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert listed.returncode in (0, 1), listed.stderr
        if not listed.stdout.strip():
            return records
        document = json.loads(listed.stdout)
        records.append(document.get("record", document))


def test_the_shim_asks_through_this_checkouts_own_bus_and_never_the_one_first_on_path(
    tmp_path: Path,
) -> None:
    """The question reaches a real channel through this checkout's bus; the stand-in never runs.

    Both halves are asserted, because either alone passes for the wrong reason. The stand-in
    was arranged first on PATH and recorded no invocation, so the shim consulted no search
    path; and the ask ended in the real bus's own named `timeout` with the question standing
    on the run's channel under that correlation, so the bus it ran was a real one over this
    checkout's configuration — which, with PATH ruled out, is this checkout's own.
    """
    bench = _bench(tmp_path)
    assert BUS.is_file() and os.access(BUS, os.X_OK), (
        f"{BUS} is not this checkout's installed bus; run 'just bootstrap' before this journey"
    )

    asked = _ask(ASK_MANAGER, bench)

    assert not bench.log.exists(), (
        f"the shim ran the onemessagebus first on PATH rather than this checkout's own:\n"
        f"{bench.log.read_text(encoding='utf-8')}"
    )
    assert asked.returncode == 1, f"an unanswered ask exited {asked.returncode}:\n{asked.stderr}"
    assert STAND_IN_ANSWER not in asked.stdout, asked.stdout
    lines = asked.stdout.splitlines()
    assert len(lines) == 1, f"the ask printed {len(lines)} lines where the bus answers with one"
    answer = json.loads(lines[0])
    assert answer["answer"] == "timeout", (
        f"an unanswered ask did not name a timeout:\n{asked.stdout}"
    )
    correlation = answer.get("correlation")
    assert correlation, f"the timeout named no question it was about:\n{asked.stdout}"
    [standing] = _queued(bench)
    assert standing["correlation"] == correlation, standing
    assert standing["message"] == QUESTION, standing
    assert standing["blocking"] is True, standing


def _checkout_whose_bus_is(tmp_path: Path, unusable: str) -> Path:
    """A checkout holding the real shim and the real configuration, and no bus it can run.

    Copied rather than pointed at, because this checkout's `.venv/bin/onemessagebus` is the
    real install and never a journey's to remove. ``unusable`` says how the bus is not one:
    absent, a directory of that name — which an executable test alone accepts — or a file
    with no execute bit, which is what an interrupted or half-copied install leaves.
    """
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "config").mkdir()
    shutil.copy2(ASK_MANAGER, checkout / "scripts" / ASK_MANAGER.name)
    shutil.copy2(CONFIG, checkout / "config" / CONFIG.name)
    bus = checkout / ".venv" / "bin" / "onemessagebus"
    match unusable:
        case "absent":
            pass
        case "a-directory":
            bus.mkdir(parents=True)
        case _:
            bus.parent.mkdir(parents=True)
            shutil.copy2(BUS, bus)
            bus.chmod(0o644)
    return checkout


@pytest.mark.parametrize(
    "unusable", ["absent", "a-directory", "not-executable"], ids=lambda value: str(value)
)
def test_a_checkout_with_no_bus_it_can_run_is_refused_naming_it_before_anything_is_asked(
    tmp_path: Path, unusable: str
) -> None:
    """Nothing runnable at the shim's own `.venv/bin`: refused by path, with the remedy.

    Each way of not being a bus this host can run reaches the same refusal, because each
    leaves the asker in the same place. A directory and a file with no execute bit are the
    two an executable test alone would accept or reject wrongly, and both are what a
    half-finished install leaves behind.

    The stand-in is still first on PATH and the real bus still behind it, and neither is
    run — the refusal is the shim's own, on stderr, at the usage exit, naming the path it
    looked at and `just bootstrap` in that checkout; nothing reaches stdout for an asker to
    read as an answer, and nothing is queued on the run's channel.
    """
    bench = _bench(tmp_path)
    checkout = _checkout_whose_bus_is(tmp_path, unusable)
    unusable_bus = checkout / ".venv" / "bin" / "onemessagebus"

    refused = _ask(checkout / "scripts" / ASK_MANAGER.name, bench)

    assert refused.returncode == 2, (
        f"a shim whose bus is {unusable} exited {refused.returncode}:\n{refused.stderr}"
    )
    assert refused.stdout == "", refused.stdout
    assert f"has no onemessagebus at {unusable_bus}" in refused.stderr, refused.stderr
    assert f"run 'just bootstrap' in {checkout}" in refused.stderr, refused.stderr
    assert not bench.log.exists(), (
        f"the shim fell back to the onemessagebus first on PATH:\n"
        f"{bench.log.read_text(encoding='utf-8')}"
    )
    assert _queued(bench) == [], "a refused ask still queued its question"
