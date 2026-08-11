"""Every delegated recipe reaches the published CLI it promises, as it promises.

This repository is a configuration layer over four published CLIs, and the `just`
recipes are the seam an operator and a planner actually touch: the names and
argument shapes are what every habit and every doc reference here depends on, and
they are meant to outlive the implementation behind them. What a wrapper owes is
exactly one thing — that the verb it names is reached, with the arguments the
recipe promised — and that is what these journeys hold it to.

They run the real `just` recipes and the real wrapper scripts. Only the published
CLI itself is doubled, at the boundary below the seam under test: each engine is
proven in its own repository, and running a real `onepipeline start` here would
launch agents.

llmlint: ignore-file[e2e_not_mocked] The published CLIs are the boundary these
wrappers delegate *to*, so a double there is what makes the delegation observable;
the recipes, the wrapper scripts, and the shell they run in are all real.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Copied into the checkout so a recipe that delegates through a script finds it.
WRAPPER_SCRIPTS = (
    "planner-verdict.sh",
    "planner-surface.sh",
    "new-persona.sh",
    "telemetry-server.sh",
    "smoke.sh",
    # `smoke.sh` names this one as the agent harness; the journeys below assert the
    # path it hands down, so the file it names has to be the real one.
    "oneharness-agent.sh",
)

#: The whole delegation table, as `just` invocation → the one command line it must
#: produce. This is the mapping this repository promises, in one place: a recipe
#: that starts naming a different verb, or dropping an argument on the way, fails
#: here rather than in an operator's terminal.
DELEGATIONS = (
    (("orchestrate", "plan.json"), "uv run onepipeline start plan.json"),
    (("orchestrate", "plan.json", "--detach"), "uv run onepipeline start plan.json --detach"),
    (("orchestrate", "--adopt", "run-1"), "uv run onepipeline adopt run-1"),
    (("run-plan", "run-1"), "uv run onepipeline round run run-1"),
    (("next-round", "run-1"), "uv run onepipeline round next run-1"),
    (("channel-next", "run-1"), "uv run onepipeline next run-1"),
    (("channel-reply", "run-1", "reply.json"), "uv run onepipeline reply run-1 reply.json"),
    (
        ("channel-surface", "run-1", "a status update"),
        "uv run onepipeline surface --kind check-in --message a status update run-1",
    ),
    (("stop", "run-1", "--force"), "uv run onepipeline stop run-1 --force"),
    (("runs", "--mine"), "uv run onepipeline runs --mine"),
    (("status", "run-1"), "uv run onepipeline status run-1"),
    (("host",), "uv run onepipeline host"),
    (("monitor", "run-1"), "uv run onepipeline monitor run-1"),
    (("results", "run-1"), "uv run onepipeline results run-1"),
    (("goals",), "uv run onepipeline goals"),
    (("telemetry", "run-1", "--breakdown"), "uv run onepipeline telemetry run-1 --breakdown"),
    (("history",), "uv run oneagentgraph history"),
    (("history-show", "oh:abc123"), "uv run oneagentgraph history show oh:abc123"),
    (("smoke",), "uv run oneagentgraph smoke"),
    (("sweep-scratch", "--dry-run"), "uv run oneagentgraph sweep --dry-run"),
    (("validate-personas",), "uv run oneagentgraph persona validate personas"),
    (("register-repo", "/checkout"), "uv run onevcs register /checkout"),
    (("repos",), "uv run onevcs repos"),
    # The published flag is spelled differently; the recipe keeps the spelling the
    # planner doctrine names and the wrapper absorbs the difference.
    (("repos", "--audit-gate-coverage"), "uv run onevcs repos --audit-gates"),
    (
        ("repo-recover", "claude/work", "--repo", "/checkout"),
        "uv run onevcs recover claude/work --repo /checkout",
    ),
    (("recoverable",), "uv run onevcs recoverable"),
    (
        ("integrate", "claude/a", "claude/b", "--push"),
        "uv run onevcs integrate claude/a claude/b --push",
    ),
    (("sync", "main"), "uv run onevcs sync main"),
)


def _checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout the real recipes run in, with `uv` traced and nothing else doubled."""
    checkout = tmp_path / "delegation"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "bin").mkdir()
    (checkout / "personas").mkdir()
    (checkout / "config").mkdir()
    shutil.copy2(ROOT / "justfile", checkout / "justfile")
    # The one source the wrappers read the read API's address from; a checkout
    # without it is not one these recipes can run in.
    shutil.copy2(ROOT / "config/read-api.address", checkout / "config/read-api.address")
    for name in WRAPPER_SCRIPTS:
        copied = checkout / "scripts" / name
        shutil.copy2(ROOT / "scripts" / name, copied)
        copied.chmod(0o755)
    trace = checkout / "trace"
    uv = checkout / "bin/uv"
    # Records the whole command line, and the reply envelope when one is piped in:
    # a verdict recipe's product is the envelope, so a trace without it would say
    # nothing about the recipe under test.
    uv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
if [ ! -t 0 ]; then
  while IFS= read -r line; do printf 'stdin %s\\n' "$line" >>"$TRACE_FILE"; done
fi
exit "${FAKE_UV_EXIT:-0}"
"""
    )
    uv.chmod(0o755)
    return checkout, trace


def _run(
    checkout: Path,
    trace: Path,
    *args: str,
    stdin: str | None = None,
    status_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{checkout / 'bin'}{os.pathsep}{environment['PATH']}"
    environment["TRACE_FILE"] = str(trace)
    environment.pop("ONEPIPELINE_RUNS_DIR", None)
    # This suite is itself run from inside a dispatch, whose real status directory and
    # history store would otherwise reach the recipe under test. Each journey states
    # the values it wants, and the default is the operator case: none of them.
    for inherited in (
        "ORCHESTRATOR_AGENT_STATUS_DIR",
        "ONEHARNESS_HISTORY_DIR",
        "ONEHARNESS_HISTORY_LABELS",
    ):
        environment.pop(inherited, None)
    if status_dir is not None:
        environment["ORCHESTRATOR_AGENT_STATUS_DIR"] = str(status_dir)
    environment.update(env or {})
    return subprocess.run(
        ["just", *args],
        cwd=checkout,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL,
    )


@pytest.mark.reads_recipes
@pytest.mark.parametrize(("invocation", "delegated"), DELEGATIONS, ids=lambda v: " ".join(v))
def test_a_delegated_recipe_reaches_its_published_verb(
    tmp_path: Path, invocation: tuple[str, ...], delegated: str
) -> None:
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [delegated]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "envelope"),
    [
        (("channel-approve", "run-1"), '{"completion": true, "reason": "approved"}'),
        (
            ("channel-reject", "run-1", "the gate never ran"),
            '{"completion": false, "reason": "the gate never ran", '
            '"message": "the gate never ran"}',
        ),
        (
            ("channel-continue", "run-1", "split the api node"),
            '{"completion": false, "reason": "split the api node", '
            '"message": "split the api node"}',
        ),
    ],
    ids=("approve", "reject", "continue"),
)
def test_a_legacy_verdict_recipe_pipes_the_envelope_reply_accepts(
    tmp_path: Path, invocation: tuple[str, ...], envelope: str
) -> None:
    """`onepipeline reply` takes one envelope; the three verdicts are how it is spelled."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "uv run onepipeline reply run-1",
        f"stdin {envelope}",
    ]


@pytest.mark.reads_recipes
def test_a_verdict_message_reaches_the_envelope_as_json_rather_than_as_text(
    tmp_path: Path,
) -> None:
    """A rejection is prose the planner typed, and prose contains quotes and newlines.

    Interpolated into a template, either one produces an envelope the CLI refuses —
    or, worse, one it accepts with the reason truncated at the first quote.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "channel-reject", "run-1", 'it said "no"; try\nagain')

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == [
        "uv run onepipeline reply run-1",
        'stdin {"completion": false, "reason": "it said \\"no\\"; try\\nagain", '
        '"message": "it said \\"no\\"; try\\nagain"}',
    ]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "reason"),
    [
        (("channel-approve", "run-1", "an extra message"), "approve does not accept a message"),
        (("channel-reject", "run-1"), "reject requires a message"),
        (("channel-continue", "run-1"), "continue requires a message"),
    ],
    ids=("approve-with-message", "reject-without", "continue-without"),
)
def test_a_verdict_recipe_refuses_a_shape_the_verdict_does_not_have(
    tmp_path: Path, invocation: tuple[str, ...], reason: str
) -> None:
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists(), "a refused verdict must not reach the channel at all"


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_supplies_the_runs_root_the_other_views_read(
    tmp_path: Path,
) -> None:
    """The published verb requires a runs root; the recipe's was optional.

    So the wrapper defaults it to the same directory `just runs` and `just status`
    read, which is what keeps the API serving the runs the planner is looking at.
    """
    checkout, trace = _checkout(tmp_path)

    assert _run(checkout, trace, "telemetry-server").returncode == 0
    assert trace.read_text().splitlines() == [
        "uv run onepipeline-api serve --runs-root runs",
    ]


@pytest.mark.reads_recipes
def test_the_telemetry_server_recipe_renders_host_and_port_as_one_bind(
    tmp_path: Path,
) -> None:
    """Two flags became one, and the recipe keeps taking both."""
    checkout, trace = _checkout(tmp_path)

    assert _run(checkout, trace, "telemetry-server", "--port", "9000").returncode == 0
    assert (
        _run(
            checkout, trace, "telemetry-server", "--runs-dir", "/elsewhere", "--host", "0.0.0.0"
        ).returncode
        == 0
    )

    assert trace.read_text().splitlines() == [
        "uv run onepipeline-api serve --runs-root runs --bind 127.0.0.1:9000",
        "uv run onepipeline-api serve --runs-root /elsewhere --bind 0.0.0.0:8765",
    ]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "delegated"),
    [
        (
            ("telemetry-server", "--runs-dir=/elsewhere"),
            "uv run onepipeline-api serve --runs-root /elsewhere",
        ),
        (
            ("telemetry-server", "--runs-root=/elsewhere"),
            "uv run onepipeline-api serve --runs-root /elsewhere",
        ),
        (
            ("telemetry-server", "--port=9000"),
            "uv run onepipeline-api serve --runs-root runs --bind 127.0.0.1:9000",
        ),
        (
            ("telemetry-server", "--host=0.0.0.0"),
            "uv run onepipeline-api serve --runs-root runs --bind 0.0.0.0:8765",
        ),
    ],
    ids=("runs-dir", "runs-root", "port", "host"),
)
def test_the_telemetry_server_recipe_takes_a_flag_in_either_spelling(
    tmp_path: Path, invocation: tuple[str, ...], delegated: str
) -> None:
    """`--flag value` and `--flag=value` are one flag, and an operator types both."""
    checkout, trace = _checkout(tmp_path)

    assert _run(checkout, trace, *invocation).returncode == 0
    assert trace.read_text().splitlines() == [delegated]


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("flag", "reason"),
    [
        ("--runs-dir", "--runs-dir needs a directory"),
        ("--host", "--host needs an address"),
        ("--port", "--port needs a port"),
    ],
    ids=("runs-dir", "host", "port"),
)
def test_the_telemetry_server_recipe_names_the_flag_it_was_given_nothing_for(
    tmp_path: Path, flag: str, reason: str
) -> None:
    """A flag with its value missing must not be forwarded as if it had one."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "telemetry-server", flag)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists(), "a refused invocation must not reach the read API at all"


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    "invocation",
    [("channel-surface", "run-1"), ("channel-surface", "run-1", "-")],
    ids=("no-text", "dash"),
)
def test_a_surface_with_no_text_is_read_from_stdin(
    tmp_path: Path, invocation: tuple[str, ...]
) -> None:
    """How an agent pipes a long update in: no text, or `-`, means read it."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation, stdin="a long update\nover two lines\n")

    assert result.returncode == 0, result.stderr
    # The trace records one line per argument break, so a multi-line message arrives
    # as the two lines it was piped in as — which is the point: the whole update
    # reaches the CLI rather than its first line.
    assert trace.read_text().splitlines() == [
        "uv run onepipeline surface --kind check-in --message a long update",
        "over two lines run-1",
    ]


@pytest.mark.reads_recipes
def test_a_surface_with_nothing_to_say_is_refused(tmp_path: Path) -> None:
    """An empty update would reset the planner's pacemaker while saying nothing."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "channel-surface", "run-1", stdin="   \n")

    assert result.returncode == 2, result.stdout
    assert "status update must be a non-empty string" in result.stderr
    assert not trace.exists()


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "could not read"),
        ("8765\n", "must hold one HOST:PORT, not '8765'"),
        ("\n", "must hold one HOST:PORT, not ''"),
    ],
    ids=("unreadable", "no-colon", "empty"),
)
def test_the_telemetry_server_recipe_refuses_an_address_file_that_is_not_one(
    tmp_path: Path, content: str | None, reason: str
) -> None:
    """Split on a colon that is not there, `--bind 8765:8765` is what would be asked for.

    The file is this repository's, not an operator's, so a broken one is a repair to
    name rather than a value to pass on — and only an invocation that needs the
    default ever reads it.
    """
    checkout, trace = _checkout(tmp_path)
    address = checkout / "config/read-api.address"
    if content is None:
        address.unlink()
    else:
        address.write_text(content, encoding="utf-8")

    result = _run(checkout, trace, "telemetry-server", "--port", "9000")

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists(), "a refused invocation must not reach the read API at all"


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    "spelling", ["--persona-dir {dir}", "--persona-dir={dir}"], ids=("space", "equals")
)
def test_the_new_persona_recipe_scaffolds_where_it_is_told_to(
    tmp_path: Path, spelling: str
) -> None:
    """A persona tree elsewhere is what a scratch persona is drafted in."""
    checkout, trace = _checkout(tmp_path)
    elsewhere = tmp_path / "scratch" / "personas"

    result = _run(
        checkout, trace, "new-persona", "draft", *spelling.format(dir=elsewhere).split(" ")
    )

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == ["uv run oneagentgraph persona new draft"]
    assert elsewhere.is_dir(), "the named directory is where the CLI has to have run"


@pytest.mark.reads_recipes
@pytest.mark.parametrize(
    ("invocation", "reason"),
    [
        (("new-persona", "--persona-dir"), "--persona-dir needs a directory"),
        (("new-persona",), "usage: new-persona.sh"),
    ],
    ids=("no-directory", "no-name"),
)
def test_the_new_persona_recipe_refuses_an_invocation_it_cannot_act_on(
    tmp_path: Path, invocation: tuple[str, ...], reason: str
) -> None:
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, *invocation)

    assert result.returncode == 2, result.stdout
    assert reason in result.stderr
    assert not trace.exists()


@pytest.mark.reads_recipes
def test_the_new_persona_recipe_scaffolds_into_this_repositorys_persona_tree(
    tmp_path: Path,
) -> None:
    """The published verb writes into the working directory, so the wrapper picks it."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "new-persona", "crozier/corpus")

    assert result.returncode == 0, result.stderr
    assert trace.read_text().splitlines() == ["uv run oneagentgraph persona new crozier/corpus"]
    # The recipe's contract is where the file lands, so the directory the CLI ran in
    # is the thing to assert: the persona tree, not wherever the operator invoked it.
    assert (checkout / "personas").is_dir()


@pytest.mark.reads_recipes
def test_the_replan_recipe_says_where_its_derivation_went(tmp_path: Path) -> None:
    """`replan` has no successor verb, and the recipe says so rather than doing something else.

    The across-round derivation is now part of the round transition itself, so the
    recipe that used to print a next-round plan names the verb that performs it —
    and reaches no CLI at all, because there is none to reach.
    """
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "replan", "plan.json", "result.json")

    assert result.returncode == 2
    assert "just next-round" in result.stderr
    assert not trace.exists()


def _live_dispatch_status_dir(tmp_path: Path) -> Path:
    """A live dispatch's status directory, in the shape the agent wrapper validates.

    `scripts/oneharness-agent.sh` accepts only `/*/orchestrator-watchdog-*/agent`, and
    the two markers below are the ones its dispatcher watches to decide whether the
    agent it launched is still alive.
    """
    status_dir = tmp_path / "orchestrator-watchdog-live" / "agent"
    status_dir.mkdir(parents=True)
    (status_dir / "agent.pid").write_text("111111\n", encoding="utf-8")
    (status_dir / "agent.done").write_text("111111\n", encoding="utf-8")
    return status_dir


#: What `scripts/oneharness-agent.sh` does to whatever `ORCHESTRATOR_AGENT_STATUS_DIR`
#: names, reduced to the two writes that matter here: it claims `agent.pid` for itself
#: and clears the terminal markers. The real wrapper is doubled at this one point
#: because it then blocks in its heartbeat loop waiting on a harness turn; the
#: hijacking it is being held to happens before that, and this reproduces it exactly.
STATUS_CLAIMING_UV = """#!/usr/bin/env bash
set -euo pipefail
printf 'uv %s\\n' "$*" >>"$TRACE_FILE"
printf 'bin %s\\n' "${ONEAGENTGRAPH_ONEHARNESS_BIN:-<unset>}" >>"$TRACE_FILE"
printf 'status %s\\n' "${ORCHESTRATOR_AGENT_STATUS_DIR:-<unset>}" >>"$TRACE_FILE"
printf 'history %s\\n' "${ONEHARNESS_HISTORY_DIR:-<unset>}" >>"$TRACE_FILE"
printf 'labels %s\\n' "${ONEHARNESS_HISTORY_LABELS:-<unset>}" >>"$TRACE_FILE"
if [ -n "${ORCHESTRATOR_AGENT_STATUS_DIR:-}" ]; then
  printf '%s\\n' "$$" >"$ORCHESTRATOR_AGENT_STATUS_DIR/agent.pid"
  rm -f "$ORCHESTRATOR_AGENT_STATUS_DIR/agent.done"
fi
"""


@pytest.mark.reads_recipes
def test_smoke_spends_its_turn_on_this_repositorys_agent_harness(tmp_path: Path) -> None:
    """The published verb generates its own config, which declares none of these identities.

    `oneagentgraph smoke` writes a throwaway `oneharness.toml` naming plain
    `claude-code` and runs plain `oneharness` against it, so a bare delegation answers
    `no harness selected` and never exercises the launch path the smoke exists to
    prove. `scripts/oneharness-agent.sh` is what forces this repository's agent
    config, and the recipe owes it.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(STATUS_CLAIMING_UV)

    result = _run(checkout, trace, "smoke")

    assert result.returncode == 0, result.stderr
    traced = trace.read_text().splitlines()
    assert traced[0] == "uv run oneagentgraph smoke"
    assert traced[1] == f"bin {checkout / 'scripts/oneharness-agent.sh'}"


@pytest.mark.reads_recipes
def test_smoke_does_not_hijack_the_live_dispatch_it_runs_inside(tmp_path: Path) -> None:
    """The regression three dead dispatches paid for.

    The pre-push hook runs `just smoke` whenever the launch path changed, and a
    dispatched agent pushes from inside its own dispatch — so this recipe runs with
    that dispatch's `ORCHESTRATOR_AGENT_STATUS_DIR` in its environment.
    `scripts/oneharness-agent.sh` takes that value as-is, claims `agent.pid` for
    itself and clears the terminal markers, so a smoke that passes its caller's value
    down hands the nested turn the liveness protocol its own dispatcher is watching.
    When that turn ends without writing `agent.done`, the dispatcher reads a tracked
    pid that is gone with no exit recorded and kills the tree: "the agent harness
    process vanished mid-turn without recording an exit".
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(STATUS_CLAIMING_UV)
    live = _live_dispatch_status_dir(tmp_path)

    result = _run(checkout, trace, "smoke", status_dir=live)

    assert result.returncode == 0, result.stderr
    handed_down = next(
        line.removeprefix("status ")
        for line in trace.read_text().splitlines()
        if line.startswith("status ")
    )
    # Isolated, and in the shape the real wrapper accepts — a smoke that picked any
    # other shape would be refused by `scripts/oneharness-agent.sh` rather than run.
    assert handed_down != str(live)
    assert Path(handed_down).name == "agent"
    assert Path(handed_down).parent.name.startswith("orchestrator-watchdog-")
    # The live dispatch's own protocol is exactly as it was.
    assert (live / "agent.pid").read_text(encoding="utf-8") == "111111\n"
    assert (live / "agent.done").read_text(encoding="utf-8") == "111111\n"
    # And the isolated directory did not outlive the run.
    assert not Path(handed_down).exists()


@pytest.mark.reads_recipes
def test_smoke_reads_back_a_history_store_nothing_else_is_writing_to(tmp_path: Path) -> None:
    """The smoke judges the record it just wrote, so it must be the only writer.

    `orchestrator-smoke` pointed the turn at its own history directory and stamped it
    with this tier's own labels. Left inheriting the ambient store, the smoke reads
    back whichever dispatch on this host wrote last and judges that instead.
    """
    checkout, trace = _checkout(tmp_path)
    (checkout / "bin/uv").write_text(STATUS_CLAIMING_UV)
    ambient = tmp_path / "ambient-history"
    ambient.mkdir()

    result = _run(
        checkout,
        trace,
        "smoke",
        env={"ONEHARNESS_HISTORY_DIR": str(ambient), "ONEHARNESS_HISTORY_LABELS": "role=agent"},
    )

    assert result.returncode == 0, result.stderr
    traced = dict(line.split(" ", 1) for line in trace.read_text().splitlines() if " " in line)
    assert traced["history"] != str(ambient)
    assert Path(traced["history"]).name == "history"
    assert traced["labels"].startswith("role=smoke,")


@pytest.mark.reads_recipes
def test_the_channel_surface_recipe_refuses_an_invocation_it_cannot_act_on(
    tmp_path: Path,
) -> None:
    """A surface with no run id names no channel, so it is a usage error, not an empty call."""
    checkout, trace = _checkout(tmp_path)

    result = _run(checkout, trace, "channel-surface")

    assert result.returncode == 2, result.stdout
    assert "usage: planner-surface.sh <run-id> [text]" in result.stderr
    assert not trace.exists()
