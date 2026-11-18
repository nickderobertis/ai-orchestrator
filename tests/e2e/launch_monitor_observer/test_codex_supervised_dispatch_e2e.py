"""A dispatch's supervisor runs on Codex under the shipped judge config, through `codex app-server`.

Every Codex-supervised dispatch on the host once died on its first supervisor turn: the
judge role's `[harness.codex] args` carried `--skip-git-repo-check`, which only `codex exec`
accepts, and a supervisor's controlled turn starts `codex app-server`, which refuses it. No
journey noticed, because every supervisor turn here was answered by `--mock-harness`, which
never checks the argv it is handed.

The judge config now skips Codex's git-repository check through the typed
`skip_git_repo_check`, which oneharness adds to `codex exec` alone, so this journey is also
what proves the setting never reaches the supervisor's `codex app-server`.

So this journey answers the supervisor one layer lower. `tests/e2e/fake_backend.py`, under
`FAKE_BACKEND_SUPERVISOR_AT_PROVIDER`, hands each supervisor turn to the real `oneharness` CLI
over the shipped `oneharness.judge.toml` with its first Codex identity pinned and nothing
mocked, so `oneharness` builds the argv that config builds and spawns `tests/e2e/fake_codex.py`
as Codex. That stand-in refuses an argument `codex app-server` rejects exactly as codex does,
and otherwise serves the turn over the app-server protocol with the verdict the stand-in
supervisor gives. A judge config carrying a flag `codex app-server` rejects fails the dispatch
at its supervisor's first turn, which is what this fails on.

Everything else is real: the `just orchestrate` recipe, the engine, the graphs, onejudge, and
the `oneharness` CLI and config chain.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] One launch of the installed
engine with the observer graph off, sharing the fixtures, stand-ins and code-keyed tier of
every other launch journey in this directory; a project of its own would be keyed on the
same workspace twice.

llmlint: ignore-file[shell_test_tiers_stay_split] A pytest journey over the real `just`
recipe rather than a shell suite.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import (
    CODEX_SERVES_APP_SERVER_ENV,
    JUDGE_CONFIG_NAME,
    PROMPT_LOG_ENV,
    SUPERVISOR_AT_PROVIDER_ENV,
    RecordedTurn,
)
from fake_codex import (
    APP_SERVER_REFUSAL_LOG_ENV,
    APP_SERVER_RELEASE,
    PromptRecord,
    RefusedLaunch,
)
from project_fixtures import project_from_plan
from test_orchestrate_launch_e2e import CandidatePlan, _node
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

# What the recipes this journey runs leave in the temporary directory is this module's own
# and goes with it (`tests/short_state.py`).
pytestmark = [pytest.mark.usefixtures("module_tmpdir")]

#: The run id `onepipeline` mints from the plan's name.
RUN_NAME = "codex-supervised-dispatch"
WORK_NODE = "work"

#: Where `tests/e2e/fake_codex.py` records each turn it was handed, argv and prompt.
CODEX_PROMPT_LOG_ENV = "FAKE_CODEX_PROMPT_LOG"

#: The subcommand a controlled Codex turn starts, a dispatch supervisor's among them.
APP_SERVER = "app-server"

SETTLING_SECONDS = 300


class Supervised(NamedTuple):
    """One launched run whose supervisor turns were answered by the Codex stand-in."""

    launch: subprocess.CompletedProcess[str]
    results: str
    #: Every supervisor turn `tests/e2e/fake_backend.py` was handed, in order.
    supervisor_turns: list[RecordedTurn]
    #: Every turn the Codex stand-in served, in order.
    codex_turns: list[PromptRecord]
    #: Every `app-server` launch the Codex stand-in refused before it started.
    refused: list[RefusedLaunch]


def _plan() -> CandidatePlan:
    """A one-node plan whose node does nothing, because its supervisor's turn is the subject."""
    return {
        "schema_version": 2,
        "name": RUN_NAME,
        "goal": {"text": "Supervise one dispatch on Codex"},
        "tasks": [
            _node(
                id=WORK_NODE,
                task=(
                    "## What\nReport.\n\n## Why\nThe supervisor's turn is the subject; the "
                    "work itself is not.\n\n## Acceptance criteria\n- Reported.\n"
                ),
            )
        ],
    }


def _just(*arguments: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout, never raising on what it answered."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(SETTLING_SECONDS),
        check=False,
    )


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


@pytest.fixture(scope="module")
def supervised(
    tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str
) -> Iterator[Supervised]:
    """Launch one node whose supervisor runs on the Codex stand-in, and read what it did."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("codex-supervised-dispatch")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(root, oneharness_bin, session=f"e2e-{RUN_NAME}")
    environment[SUPERVISOR_AT_PROVIDER_ENV] = "1"
    backend_log = root / "turns.jsonl"
    environment[PROMPT_LOG_ENV] = str(backend_log)
    codex_log = root / "codex-turns.jsonl"
    environment[CODEX_PROMPT_LOG_ENV] = str(codex_log)
    refusal_log = root / "codex-refusals.jsonl"
    environment[APP_SERVER_REFUSAL_LOG_ENV] = str(refusal_log)
    plan = root / "plan.json"
    plan.write_text(json.dumps(_plan()), encoding="utf-8")
    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        # The observer graph watches a run and dispatches nothing, so it has nothing to say
        # about a node's supervisor, and it costs two members a turn each per launch.
        "--dag-graph",
        "off",
        environment=environment,
    )
    reported = _just("results", RUN_NAME, environment=environment)
    turns: list[RecordedTurn] = [json.loads(line) for line in _lines(backend_log)]
    yield Supervised(
        launch=launch,
        results=f"{reported.stdout}\n{reported.stderr}",
        supervisor_turns=[
            turn for turn in turns if Path(turn["config"] or "").name == JUDGE_CONFIG_NAME
        ],
        codex_turns=[json.loads(line) for line in _lines(codex_log)],
        refused=[json.loads(line) for line in _lines(refusal_log)],
    )


@pytest.mark.xdist_group("codex-supervised-dispatch")
def test_the_dispatch_settles_with_its_supervisor_on_codex_app_server(
    supervised: Supervised,
) -> None:
    """The run settles, and the supervisor's verdict turns ran on `codex app-server`.

    A supervisor's verdict is a controlled turn, which oneharness runs on `codex app-server`;
    the done-when evaluation after it is an `exec` turn. A judge config carrying an argument
    `codex app-server` rejects fails here: the stand-in refuses the supervisor's first turn as
    codex does, the dispatch has no supervisor, and the run does not settle with its node done.
    """
    assert not supervised.refused, (
        f"the Codex stand-in refused the supervisor's `codex app-server` as {APP_SERVER_RELEASE} "
        "refuses it, so oneharness.judge.toml's [harness.codex] args carry an argument "
        f"`codex app-server` rejects: {supervised.refused}"
    )
    assert supervised.launch.returncode == 0, (
        f"the run did not settle with its supervisor on Codex ({APP_SERVER_RELEASE}'s "
        f"`codex app-server` accepts only what tests/e2e/fake_codex.py states):\n"
        f"{supervised.results}\n{supervised.launch.stdout}\n{supervised.launch.stderr}"
    )
    assert "done" in supervised.results, supervised.results
    assert supervised.supervisor_turns, "the dispatch reached no supervisor turn"
    first = supervised.supervisor_turns[0]
    assert first["control"], (
        f"the supervisor's first turn was not a controlled one, so it never reached "
        f"`codex app-server`: {first}"
    )
    assert len(supervised.codex_turns) == len(supervised.supervisor_turns), (
        "a supervisor turn did not reach the Codex stand-in, or one reached it twice: "
        f"{supervised.codex_turns}"
    )
    served = [turn for turn in supervised.codex_turns if turn["argv"][:1] == [APP_SERVER]]
    controlled = [turn for turn in supervised.supervisor_turns if turn["control"]]
    assert len(served) == len(controlled), (
        f"{len(controlled)} controlled supervisor turn(s), but the Codex stand-in, which "
        f"{CODEX_SERVES_APP_SERVER_ENV} has serve `codex app-server`, served {len(served)}: "
        f"{supervised.codex_turns}"
    )
    assert supervised.codex_turns[0]["argv"][:1] == [APP_SERVER], supervised.codex_turns[0]
