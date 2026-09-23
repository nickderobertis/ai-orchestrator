"""A dispatch under a config that `extends` a parent really runs under the parent's values.

`FileConfig` refuses an unknown key, so a reader predating `extends` refuses such a file
at the first read — and one accepting the key while ignoring the parent would dispatch
under a config that says almost nothing, which is worse, because it looks like it worked.
Neither is safe to take from a release note, so the whole dispatch path is driven here,
once per road a run's driver reaches it by:

* `just orchestrate` on a one-node plan whose `worker` member names a **child** config
  declaring `extends`. What the parent states and the child does not is the answer the
  provider gives, through `[env] MOCK_STDOUT` — the variable `oneharness`'s own mock
  responder reads — so the sentence the run records as the worker's turn is one only the
  parent could have produced. It is read off the run's journal rather than off any config
  file, which is the distinction that matters: a config on disk says what this repository
  wrote down, and the journal says what the provider was handed.
* the same plan, stopped, then adopted through `POST /api/v2/runs/{run}/adopt` on the
  read API `just telemetry-server` serves. That route retains the API's **own** binary as
  the driver, so `config/onepipeline-ui.version` governs the dispatch it then makes, and
  a reader linking an engine that cannot resolve a chain would re-dispatch the node under
  a config with no identities in it.

* the `oneharness` CLI this host runs itself, which reads four of the ten role files
  on its own account rather than through the engine, so its own pin has to resolve a
  chain too. That road is read off the effective configuration the CLI reports, values
  and attributions together.

And the refusal down each of the two dispatch roads, because "the parent is resolved"
and "the parent is ignored" are only told apart by a chain that cannot resolve: a child
naming a parent that is not there settles its node rather than dispatching it, and the
settlement names the config.

Only the paid provider is substituted, at the seam every launch journey here substitutes
it: `tests/e2e/fake_backend.py` delegates each turn to the real `oneharness` CLI with
`--mock-harness`, so the config chain is resolved by the real loader.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] Two launches of the
installed engine, sharing the fixtures and stand-ins of the code-keyed tier every other
launch journey in this directory sits in; a project of its own would be keyed on the same
workspace twice.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys over the real
`just` recipes rather than a shell suite.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] No marker selects a tier
here beyond the xdist group that keeps these launches off the toolchain lock.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_backend import AGENT_DELAY_ENV
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import project_from_plan
from test_orchestrate_launch_e2e import CandidatePlan, _node
from test_orchestrate_launch_e2e import _environment as _launched_environment
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.root import REPO_ROOT

#: Every launch here blocks on a `just` recipe that blocks on `uv run`, which waits on
#: this checkout's `.venv` lock; `tests/e2e/nx_workspace.py` is where that constraint is
#: named for the whole tier.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

WORK_NODE = "work"

#: What the parent — and only the parent — makes the provider answer. `oneharness`'s
#: shipped mock responder prints whatever `MOCK_STDOUT` holds, and `oneharness` gives a
#: config's `[env]` to every provider process it starts, so a turn whose answer is this
#: sentence is one whose provider was handed a value that lives only in the parent file.
#: `tests/e2e/fake_backend.py` sets `MOCK_STDOUT` in the environment it passes down, and
#: the config's own `[env]` beats it — which is the whole reason this is evidence rather
#: than a restatement: an unresolved chain leaves the stand-in's own answer in place.
PARENT_ONLY_ANSWER = "answered under a value that lives only in the parent config"

#: The parent every child below names: the identities and the answer, and nothing the
#: child repeats. `harnesses` is here rather than in the child for the same reason the
#: refactor this adoption unblocks puts it in a parent — it is the shared block.
PARENT_CONFIG = f"""\
harnesses = ["codex"]
timeout = 600

[env]
MOCK_STDOUT = {json.dumps(json.dumps({"result": PARENT_ONLY_ANSWER}))}
"""

#: The child: one key, and it is the one the adoption is about. The path is relative
#: because that is the form the refactor writes — `extends` resolves against the
#: directory the declaring file sits in, and `oneagentgraph` anchors it to an absolute
#: path when it stamps this file into the member's scratch.
CHILD_CONFIG = 'extends = "parent.toml"\n'

#: A child whose parent is not there, for the refusal. Written as a name nothing
#: creates rather than as a path outside the directory, so what fails is the resolution
#: and not a permission.
CHILD_WITHOUT_A_PARENT = 'extends = "no-such-parent.toml"\n'

#: How a member's harness config is named on a launch, which is the published override
#: `AGENTS.md` gives for pairing the sides differently for one run.
AGENT_CONFIG_OVERRIDE = "members.worker.agent.oneharness_config"

SETTLING_SECONDS = 300

#: How long the stand-in holds the adopt journey's agent turn before answering. Long
#: enough that the stop lands on a dispatch that has not answered yet — which is what
#: makes the answer the adopted driver's rather than the stopped driver's — and short
#: enough that the re-dispatch answers inside the wait below.
HELD_SECONDS = 20

#: The engine's refusal of an adoption of a run another session owns, which is why the
#: server below is started under the same session the run was launched from.
NOT_OWNER = 409

#: What the engine settles a node as when its dispatch could not be prepared at all —
#: the word `AGENTS.md` gives a host that could not launch, and the one a config chain
#: that cannot be read reaches, because the read happens in the dispatch-environment
#: preflight and before any turn. Asserted rather than left to a substring match: a
#: chain that was never read would let the dispatch happen and settle some other way.
REFUSED_BEFORE_DISPATCH = "infrastructure-failure"


class Launch(NamedTuple):
    """One launched run, and the two operator views that answer for it.

    Both reads go through a recipe rather than through the run's own records: `just
    transcript` is the read `AGENTS.md` names for a settled node's evidence, and `just
    results` is where an outcome and its detail are reported. The journal beneath them
    is the engine's, and a journey that parsed it would be asserting on a shape no
    operator sees.
    """

    environment: dict[str, str]
    run: str
    runs_root: Path

    def answers(self) -> list[str]:
        """Every rendered turn line carrying the sentence only the parent states.

        Empty while the run has produced no turn to render, which is what makes this
        usable as a wait as well as an assertion.
        """
        rendered = _just("transcript", self.run, environment=self.environment, seconds=120)
        if rendered.returncode != 0:
            return []
        return [line for line in rendered.stdout.splitlines() if PARENT_ONLY_ANSWER in line]

    def results(self) -> str:
        """What `just results` reports for this run, outcomes and details together."""
        reported = _just("results", self.run, environment=self.environment, seconds=120)
        return f"{reported.stdout}\n{reported.stderr}"

    def reads(self) -> bool:
        """Whether `just status` answers for this run yet, which is that it is driving."""
        return _just("status", self.run, environment=self.environment, seconds=120).returncode == 0


def _plan(name: str) -> CandidatePlan:
    """A one-node plan whose node does nothing, because the turn is the subject."""
    return {
        "schema_version": 2,
        "name": name,
        "goal": {"text": "Dispatch one node under a config that extends a parent"},
        "tasks": [
            _node(
                id=WORK_NODE,
                task=(
                    "## What\nReport.\n\n## Why\nThe answer the provider gives is the "
                    "subject; the work itself is not.\n\n## Acceptance criteria\n"
                    "- Reported.\n"
                ),
            )
        ],
    }


def _chain(root: Path, *, child: str = CHILD_CONFIG) -> Path:
    """Write the two files in one directory, which is what `extends` resolves against."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "parent.toml").write_text(PARENT_CONFIG, encoding="utf-8")
    written = root / "child.toml"
    written.write_text(child, encoding="utf-8")
    return written


def _just(
    *arguments: str, environment: dict[str, str], seconds: float = SETTLING_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Run one real recipe from this checkout, never raising on what it answered."""
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _launch(
    tmp_path: Path,
    oneharness_bin: str,
    *,
    run: str,
    child: str = CHILD_CONFIG,
    detached: bool = False,
    held: float | None = None,
) -> tuple[Launch, subprocess.CompletedProcess[str]]:
    """Launch one plan against `child`, under a session and roots of this journey's own.

    `_launched_environment` is where the paid provider is substituted and where the run's
    registry, worktrees, ledger and graph scratch are pointed away from the host's; what
    is added here is the config chain the launch names and the hold the adopt journey
    needs.
    """
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = _launched_environment(tmp_path, oneharness_bin, session=f"e2e-{run}")
    if held is not None:
        environment[AGENT_DELAY_ENV] = str(held)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(_plan(run)), encoding="utf-8")
    launched = _just(
        "orchestrate",
        project_from_plan(plan),
        # The observer graph watches a run and dispatches nothing, so it has nothing to
        # say about which config a node's own member ran under, and it costs two members
        # a turn each per launch.
        "--dag-graph",
        "off",
        "--node-set",
        f"{AGENT_CONFIG_OVERRIDE}={_chain(tmp_path / 'chain', child=child)}",
        *(("--detach",) if detached else ()),
        environment=environment,
    )
    return Launch(environment, run, Path(environment["ONEPIPELINE_RUNS_DIR"])), launched


@pytest.fixture(scope="module")
def dispatched(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Iterator[Launch]:
    """One settled run, shared by every question that reads a completed dispatch."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("extends-chain-dispatch")
    launch, launched = _launch(tmp_path, oneharness_bin, run="extends-chain-dispatch")
    assert launched.returncode == 0, (
        f"the launch did not settle:\n{launched.stdout}\n{launched.stderr}"
    )
    try:
        yield launch
    finally:
        _just("stop", launch.run, environment=launch.environment, seconds=120)


def test_a_dispatch_runs_under_a_value_only_its_configs_parent_states(
    dispatched: Launch,
) -> None:
    """The dispatch's provider answered out of the parent, so the chain really resolved.

    The failing shape is the quiet one: a dispatch path that accepted `extends` and read
    the one document would hand the provider no `[env]` at all, the stand-in's own answer
    would stand, and every pin on this host would read current. So what is asserted is
    the presence of a sentence the child never states.
    """
    carried = dispatched.answers()

    assert carried, (
        f"no turn of {dispatched.run} answered out of the parent config; the worker's "
        "agent side named a child declaring `extends`, and `just transcript` renders no "
        f"answer only the parent could have produced:\n{dispatched.results()}"
    )


def test_a_child_whose_parent_is_missing_settles_the_node_rather_than_dispatching_it(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The other half: a chain that cannot resolve is refused, and the refusal names it.

    Without this the journey above is weaker than it reads. A dispatch path that ignored
    `extends` entirely would pass nothing here and fail there — but one that read the
    child and silently dropped an unreadable parent would leave both green while the
    parent's identities reached nothing. What says the chain is *read* is that an
    unreadable one stops the node.
    """
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    launch, launched = _launch(
        tmp_path,
        oneharness_bin,
        run="extends-chain-unresolvable",
        child=CHILD_WITHOUT_A_PARENT,
    )
    try:
        assert launched.returncode != 0, (
            "the run settled every node done although one named a parent that is not "
            f"there:\n{launched.stdout}\n{launched.stderr}"
        )
        assert not launch.answers(), (
            "a node whose config names a parent that is not there answered out of a "
            "parent anyway, so the chain was not read at the dispatch"
        )
        reported = launch.results()
        assert f"failed ({REFUSED_BEFORE_DISPATCH})" in reported, (
            f"a node whose config chain could not be read did not settle "
            f"{REFUSED_BEFORE_DISPATCH!r}; the refusal happens before anything is "
            f"dispatched, so no other outcome is the chain being read:\n{reported}"
        )
        assert "child.toml" in reported and "no-such-parent.toml" in reported, (
            "the settlement named neither the config that declared the parent nor the "
            f"parent it could not read, so it says nothing an operator could act on:\n"
            f"{reported}"
        )
    finally:
        _just("stop", launch.run, environment=launch.environment, seconds=120)


def _free_port() -> int:
    """A port the read API can bind, chosen per journey rather than taken from config."""
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        return int(bound.getsockname()[1])


def _post(url: str) -> tuple[int, bytes]:
    """One mutation on the read API, whose refusals answer as much as its successes."""
    request = urllib.request.Request(url, data=b"{}", method="POST")
    request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=e2e_timeout(30)) as answered:
            return answered.status, answered.read()
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read()


@pytest.fixture
def read_api(stopped: Launch) -> Iterator[str]:
    """`just telemetry-server` over one run's root, as the session that launched it.

    Started under the launching run's own session because the adopt route is held to
    ownership: a server that could not name itself as the owner would be refused
    `409 not_owner`, and this journey would be measuring that refusal rather than what
    the adopted reader drives.
    """
    port = _free_port()
    serving = {**stopped.environment, "ONEPIPELINE_RUNS_DIR": str(stopped.runs_root)}
    started = subprocess.Popen(  # noqa: S603 - this repository's own recipe
        [
            "just",
            "telemetry-server",
            "--runs-dir",
            str(stopped.runs_root),
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=serving,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        until(
            "the read API to answer",
            lambda: _answers(f"{base}/healthz"),
            seconds=60,
            state=lambda: f"the server's exit status is {started.poll()}",
            interval=0.2,
        )
        yield base
    finally:
        started.terminate()
        started.wait(timeout=e2e_timeout(30))


def _answers(url: str) -> bool:
    """Whether `url` answers at all — a refusal counts, since what is awaited is a bind."""
    try:
        with urllib.request.urlopen(url, timeout=2):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False


@pytest.fixture
def stopped(tmp_path: Path, oneharness_bin: str) -> Iterator[Launch]:
    """One run of the same plan, launched detached and then stopped, ready to be adopted."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    launch, launched = _launch(
        tmp_path,
        oneharness_bin,
        run="extends-chain-adopted",
        detached=True,
        held=HELD_SECONDS,
    )
    assert launched.returncode == 0, (
        f"the detached launch did not start:\n{launched.stdout}\n{launched.stderr}"
    )
    # The stop has to land on a dispatch that is in flight and has not answered. A stop
    # before the first dispatch would leave the adopted driver making the run's only
    # dispatch — still a fair journey — but a stop *after* the node answered would leave
    # nothing to re-dispatch, and the wait below would be satisfied by the stopped
    # driver's own answer. So the fixture waits for the dispatch and then checks that no
    # answer has been recorded yet.
    until(
        "the detached driver to answer for its run",
        launch.reads,
        seconds=120,
        state=launch.results,
        interval=0.5,
    )
    stop = _just("stop", launch.run, environment=launch.environment, seconds=120)
    assert stop.returncode == 0, f"the run could not be stopped:\n{stop.stdout}\n{stop.stderr}"
    assert not launch.answers(), (
        "the stopped driver had already answered out of the parent config, so anything "
        "the adopted driver goes on to record would be indistinguishable from it"
    )
    try:
        yield launch
    finally:
        _just("stop", launch.run, environment=launch.environment, seconds=120)


def test_a_run_adopted_from_the_read_api_dispatches_under_the_parents_value(
    stopped: Launch, read_api: str
) -> None:
    """The adopt route retains the API's own binary, so its engine is what dispatches.

    That is what makes `config/onepipeline-ui.version` a pin that governs a dispatch on
    this host, and it is why `tests/test_linked_libraries.py` holds it level with
    `config/onepipeline.version`. Here the level pair is spent: a reader whose engine
    could not resolve a chain would re-dispatch this node under a config stating no
    identities, and no turn of the adopted run would answer out of the parent.
    """
    status, body = _post(f"{read_api}/api/v2/runs/{stopped.run}/adopt")

    assert status != NOT_OWNER, (
        f"the read API was refused the adoption as another session's run: {body!r}; it "
        "is started under the session the run was launched from"
    )
    assert status < 300, f"the adoption was refused: {status} {body!r}"

    until(
        "the adopted run to answer out of the parent config",
        lambda: bool(stopped.answers()),
        seconds=SETTLING_SECONDS,
        state=stopped.results,
        interval=1.0,
    )


def test_an_adopted_run_whose_parent_went_away_settles_rather_than_dispatching(
    stopped: Launch, read_api: str, tmp_path: Path
) -> None:
    """The refusal down the adopt road, which is a different build's copy of the engine.

    The refusal above is `onepipeline-cli`'s; this one is the read API's own statically
    linked engine, a separate wheel under a separate pin, so a reader that accepted
    `extends` and dropped the parent would pass the journey above — answering out of a
    parent it never read — and fail only here.

    Removing the parent between the stop and the adopt is the only way this road reaches
    an unresolvable chain: the refusal happens in the dispatch-environment preflight, so
    a run launched with one settles before there is an in-flight dispatch to stop and
    adopt. What it asks of the adopted driver is that it resolve the chain at the
    dispatch it makes, against the files as they stand then.
    """
    (tmp_path / "chain" / "parent.toml").unlink()

    status, body = _post(f"{read_api}/api/v2/runs/{stopped.run}/adopt")
    assert status != NOT_OWNER, (
        f"the read API was refused the adoption as another session's run: {body!r}"
    )
    assert status < 300, f"the adoption was refused: {status} {body!r}"

    until(
        "the adopted run to settle the node whose chain it could not read",
        lambda: f"failed ({REFUSED_BEFORE_DISPATCH})" in stopped.results(),
        seconds=SETTLING_SECONDS,
        state=stopped.results,
        interval=1.0,
    )
    assert not stopped.answers(), (
        "the adopted driver answered out of a parent that is no longer there, so what "
        "it dispatched under came from something other than the file the child names"
    )
    reported = stopped.results()
    assert "child.toml" in reported and "parent.toml" in reported, (
        "the settlement named neither the config that declared the parent nor the "
        f"parent it could not read, so it says nothing an operator could act on:\n"
        f"{reported}"
    )


def test_the_adopted_cli_resolves_a_chain_and_attributes_it_to_the_parent(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The third road: the `oneharness` CLI this host runs itself, reading a chain.

    `config/onepipeline.version` governs the two roads above, but four of this host's
    ten role files are read by the CLI `config/oneharness.version` names rather than by
    the engine — `oneharness.toml` and `oneharness.judge.toml` through
    `config/onejudge.base.yaml`'s `bin: oneharness`, and `oneharness.llmlint.toml` and
    `oneharness.plan-review.toml` named on a command line — so a CLI pin that could not
    resolve a chain would refuse those four at the first read while both dispatch roads
    stayed green.

    The `source` annotation is asserted beside the value because the two failures this
    guards are told apart by it: a CLI that ignored `extends` would report the built-in
    default, and one that merged the parent without recording where a value came from
    would leave an operator unable to see which file to edit.
    """
    child = _chain(tmp_path / "chain")
    parent = str((tmp_path / "chain" / "parent.toml").resolve())

    # Every `ONEHARNESS_*` override beats every file, and this suite is itself run from
    # inside a dispatch that sets them, so an inherited one would be read as the
    # parent's value — `tests/e2e/test_oneharness_timeout_e2e.py` drops them for the
    # same reason.
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")
    }
    resolved = subprocess.run(
        [oneharness_bin, "config", "--config", str(child), "--compact"],
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert resolved.returncode == 0, (
        f"the adopted `oneharness` refused a child declaring `extends`:\n{resolved.stderr}"
    )
    effective: dict[str, object] = json.loads(resolved.stdout)

    for field, stated in (("harnesses", ["codex"]), ("timeout", 600)):
        assert effective[field] == {"value": stated, "source": parent}, (
            f"the effective configuration's `{field}` is {effective[field]!r}; the child "
            f"states no `{field}`, so a resolved chain reports {stated!r} attributed to "
            f"{parent}"
        )
