"""Codex dispatches share one app-server, one model catalog, and keep analytics off.

The releases this host adopts (oneharness https://github.com/nickderobertis/oneharness/pull/1467
and #1464, first in `oneharness-core` 0.28.0) change three things about every Codex
dispatch, each by default and none by a key any role file here sets:

- concurrent controlled turns of one configuration run on **one pooled app-server**,
  and each turn's commands still see only that dispatch's own environment, carried on
  its thread — so the `XDG_RUNTIME_DIR` repoint `oneharness.dispatch.toml` derives from
  each node's `ONEPIPELINE_NODE_SCRATCH_DIR`, and the run id, stay per dispatch;
- a launch reads oneharness's **shared model catalog** file, refreshed by one short
  `codex app-server` from the `models_cache.json` Codex writes in its home, rather than
  fetching the catalog itself;
- a launch passes `-c analytics.enabled=false`.

Each is driven through the pinned `oneharness` CLI under this repository's worker config.
The shared server is driven with oneharness's own `--mock-harness codex` responder,
which runs a turn's command under its thread's environment as Codex does; the catalog
and analytics with a recording `codex` first on `PATH`, because only the argv Codex was
started with says which catalog and analytics settings it was handed.
`tests/test_linked_libraries.py` holds both the engine's linked core and the CLI's own at
or past the release that brought these defaults.
"""

# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] Nothing here is expensive:
# Codex is doubled, no model turn is spent, and the CLI driven is the one this workspace's
# own lockfile installs into `.venv`, the same tier and CLI as
# tests/e2e/launch_monitor_observer/test_codex_plugins_off_e2e.py beside it.

from __future__ import annotations

import json
import subprocess
import sys
import time
import tomllib
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import pytest
import short_state
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

FAKE_CODEX = helper("fake_codex.py")
#: The agent side's role config: the one a dispatched worker's turn runs against, over
#: `oneharness.dispatch.toml`, which repoints `XDG_RUNTIME_DIR` per dispatch.
AGENT_CONFIG = REPO_ROOT / "oneharness.toml"
#: Every role file and both shared parents: each is resolved, and none may opt out.
ROLE_FILES = sorted(REPO_ROOT.glob("oneharness*.toml"))
#: The three settings whose defaults this adoption puts in force, and the opt-out value
#: of each that no role file here may write.
OPT_OUTS = {"app_server": "per-dispatch", "shared_model_catalog": False, "analytics": True}
#: The version the recording Codex reports: half of the shared catalog's key.
CODEX_VERSION = "codex-cli 0.163.0"
#: The identity the dispatch turns run as: the first Codex identity of the worker's chain.
IDENTITY = "codex:primary"

# llmlint: ignore-block[e2e_not_mocked] Codex is the paid provider, the one boundary AGENTS.md's realistic-tests invariant doubles: a real Codex cannot be made to report its own argv, and the subject — the pinned `oneharness` CLI under this repository's worker config, refreshing its catalog and launching the turn — runs for real.  # noqa: E501 - a directive is one line
RECORDING_CODEX = f"""#!{sys.executable}
import datetime, json, os, sys

argv = sys.argv[1:]
if argv == ["--version"]:
    print({CODEX_VERSION!r})
    sys.exit(0)
with open(os.environ["RECORDED_CODEX_LAUNCHES"], "a", encoding="utf-8") as log:
    log.write(json.dumps({{"argv": argv, "home": os.environ.get("CODEX_HOME")}}) + "\\n")
if argv[:1] != ["app-server"]:
    os.execv(sys.executable, [sys.executable, {str(FAKE_CODEX)!r}, *argv])
# The catalog refresh: like Codex, fetch the catalog into this home's cache on start
# unless handed a catalog file, then answer `initialize`.
if not any(word.startswith("model_catalog_json=") for word in argv):
    fetched = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds")
    cache = {{
        "fetched_at": fetched.replace("+00:00", "Z"),
        "client_version": {CODEX_VERSION.split()[1]!r},
        "models": [{{"slug": "recorded-model"}}],
    }}
    cache_path = os.path.join(os.environ["CODEX_HOME"], "models_cache.json")
    with open(cache_path, "w", encoding="utf-8") as written:
        json.dump(cache, written)
for line in sys.stdin:
    request = json.loads(line)
    if request.get("method") == "initialize":
        print(json.dumps({{"jsonrpc": "2.0", "id": request["id"],
                          "result": {{"userAgent": "recording-codex"}}}}), flush=True)
"""
# llmlint: ignore-end[e2e_not_mocked]


class Launch(NamedTuple):
    """One process the recording `codex` was started as, `--version` probes aside."""

    argv: list[str]
    home: str | None


def config_value(argv: Sequence[str], key: str) -> str | None:
    """The last `-c`/`--config` value Codex reads for `key` off `argv`, unquoted."""
    value: str | None = None
    for index, word in enumerate(argv[:-1]):
        if word in ("-c", "--config"):
            name, separator, assigned = argv[index + 1].partition("=")
            if separator and name.strip() == key:
                value = assigned.strip().strip("\"'")
    return value


def _base_environment(tmp_path: Path) -> dict[str, str]:
    """A host with nothing inherited: no `ONEHARNESS_*`, no ambient Codex home."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(tmp_path / "home" / ".config"),
    }


# llmlint: ignore-block[shell_test_tiers_stay_split] A pytest journey, not a shell suite: it runs the `oneharness` this workspace's own lockfile installs into `.venv` against a local stand-in, in about a second, the same tier and CLI as tests/e2e/launch_monitor_observer/test_codex_plugins_off_e2e.py.  # noqa: E501 - a directive is one line
def test_a_dispatch_launch_reads_the_shared_catalog_with_analytics_off(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The catalog refresh runs once, and the turn's launch names its file."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex = bin_dir / "codex"
    codex.write_text(RECORDING_CODEX, encoding="utf-8")
    codex.chmod(0o755)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text("", encoding="utf-8")
    scratch = tmp_path / "node-scratch"
    scratch.mkdir()
    state = tmp_path / "state"
    log = tmp_path / "codex-launches.ndjson"
    environment = {
        **_base_environment(tmp_path),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "XDG_STATE_HOME": str(state),
        "CODEX_HOME": str(codex_home),
        "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
        "RECORDED_CODEX_LAUNCHES": str(log),
    }

    completed = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(AGENT_CONFIG),
            "--harness",
            IDENTITY,
            "--prompt",
            "Say hello.",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    launches = [Launch(**json.loads(line)) for line in log.read_text("utf-8").splitlines()]
    assert [launch.argv[:1] for launch in launches] == [["app-server"], ["exec"]], launches
    refresh, turn = launches
    assert config_value(refresh.argv, "model_catalog_json") is None, refresh
    catalog = (
        state
        / "oneharness"
        / "codex-model-catalog"
        / f"{uuid.uuid5(uuid.NAMESPACE_OID, f'{codex_home}\n{CODEX_VERSION}').hex}.json"
    )
    assert json.loads(catalog.read_text("utf-8")) == {"models": [{"slug": "recorded-model"}]}
    assert config_value(turn.argv, "model_catalog_json") == str(catalog), (
        f"the dispatch's Codex launch should read oneharness's shared catalog {catalog} "
        f"rather than fetch its own: {turn.argv}"
    )
    for launch in launches:
        assert config_value(launch.argv, "analytics.enabled") == "false", (
            f"a Codex launch went without analytics off, so it uploads analytics: {launch}"
        )


# llmlint: ignore-end[shell_test_tiers_stay_split]


class Turn(NamedTuple):
    """One controlled dispatch turn, and what its command recorded."""

    run_id: str
    scratch: Path
    seen: Path
    process: subprocess.Popen[str]


def _start_turn(
    tmp_path: Path, name: str, oneharness_bin: str, release: Path, server: dict[str, str]
) -> Turn:
    codex_home = tmp_path / "codex-home"
    scratch = tmp_path / f"scratch-{name}"
    scratch.mkdir()
    seen = tmp_path / f"seen-{name}.txt"
    run_id = f"run-{name}-{uuid.uuid4().hex[:8]}"
    # This test's own short state root: the pool's socket and the sessions' control
    # sockets live under it, which a root under `tmp_path` puts past the platform's
    # socket-address limit when the suite's temporary root is deep, as the gate's is.
    # Under the process's short base, whose teardown waits out the shared server's
    # linger before removing it.
    state = short_state.state_home(tmp_path)
    environment = {
        **_base_environment(tmp_path),
        "XDG_STATE_HOME": str(state),
        "CODEX_HOME": str(codex_home),
        "ONEPIPELINE_RUN_ID": run_id,
        "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
        # Per-thread knobs of oneharness's mock app-server: the command each turn runs,
        # and a file whose existence ends the turn, so both turns are live at once.
        "MOCK_CODEX_RUN_COMMAND": (
            f'printf "%s\\n%s\\n" "$ONEPIPELINE_RUN_ID" "$XDG_RUNTIME_DIR" > {seen}'
        ),
        "MOCK_CODEX_HOLD_UNTIL": str(release),
        "MOCK_STDOUT": json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}
        ),
        **server,
    }
    process = subprocess.Popen(
        [
            oneharness_bin,
            "run",
            "--config",
            str(AGENT_CONFIG),
            "--harness",
            IDENTITY,
            # llmlint: ignore[e2e_not_mocked] Only the paid Codex process is replaced.
            "--mock-harness",
            "codex",
            "--prompt",
            "Run the command.",
            "--control",
            "--mode",
            "bypass",
            "--session",
            f"sas-{name}",
            "--session-dir",
            str(state / "sessions"),
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return Turn(run_id=run_id, scratch=scratch, seen=seen, process=process)


class Ended(NamedTuple):
    """A turn that ended: what its command saw and which server its report names."""

    turn: Turn
    seen: list[str]
    server: object


def _two_concurrent_turns(
    tmp_path: Path, oneharness_bin: str, server: dict[str, str]
) -> list[Ended]:
    """Run two dispatch turns held live together until both commands have run."""
    (tmp_path / "codex-home").mkdir()
    (tmp_path / "codex-home" / "config.toml").write_text("", encoding="utf-8")
    release = tmp_path / "release"
    turns = [_start_turn(tmp_path, name, oneharness_bin, release, server) for name in ("a", "b")]
    try:
        deadline = time.monotonic() + e2e_timeout(60)
        while not all(turn.seen.exists() for turn in turns):
            exited = [
                turn.process.communicate() for turn in turns if turn.process.poll() is not None
            ]
            assert not exited, (
                f"a turn ended before both turns' commands ran, so the two were never live "
                f"together on one shared app-server: {exited}"
            )
            assert time.monotonic() < deadline, "both turns' commands should have run"
            time.sleep(0.1)
    finally:
        # Each turn is held until this file exists, so both commands ran while both
        # turns were live; releasing them lets every turn end on its own.
        release.touch()
        outputs = [turn.process.communicate(timeout=e2e_timeout(60)) for turn in turns]

    ended = []
    for turn, (stdout, stderr) in zip(turns, outputs, strict=True):
        assert turn.process.returncode == 0, stdout + stderr
        report = json.loads(stdout)
        (result,) = report["results"]
        assert result["status"] == "ok", result
        seen = turn.seen.read_text("utf-8").splitlines()
        ended.append(Ended(turn=turn, seen=seen, server=report["control"]["app_server"]))
    return ended


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] The adopting task's acceptance criteria name this module as where these two journeys run and pass, so they stay here; each pays the shared server's documented 60 s linger at its own teardown, which oneharness 0.26.1 gives no setting to shorten, and a narrowly keyed project for the pooled-server journeys is drafted as a follow-up.  # noqa: E501 - a directive is one line
def test_two_concurrent_dispatch_turns_share_one_server_and_keep_their_own_environment(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Both turns are live together on one server; each command sees only its own values."""
    ended = _two_concurrent_turns(tmp_path, oneharness_bin, {})

    for each in ended:
        assert each.seen == [each.turn.run_id, str(each.turn.scratch)], (
            f"the command of the turn for {each.turn.run_id} saw another dispatch's "
            f"environment: {each.seen}"
        )
    servers = [each.server for each in ended]
    assert servers[0] == servers[1], f"the two dispatches ran on different servers: {servers}"


def test_a_server_leaking_one_threads_environment_is_read_as_leaking(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The control: the reading above can fail.

    oneharness's mock app-server takes a server-wide `leak-env` fault, under which every
    command runs in the first opened thread's environment — the defect a shared server
    must not have. It is a `CODEX_*` variable, which the server's scrubbed environment
    keeps, so both turns still pool onto one server, and one of them sees the other's.
    """
    ended = _two_concurrent_turns(tmp_path, oneharness_bin, {"CODEX_MOCK_FAULT": "leak-env"})

    assert ended[0].server == ended[1].server
    assert ended[0].seen == ended[1].seen, [each.seen for each in ended]


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


@pytest.mark.parametrize("role", ROLE_FILES, ids=[path.name for path in ROLE_FILES])
def test_every_role_resolves_the_shared_defaults_for_every_codex_identity(
    role: Path, oneharness_bin: str
) -> None:
    """No role file opts out, and the pinned CLI resolves each to the shared defaults."""
    declared = tomllib.loads(role.read_text("utf-8"))
    codex = declared.get("harness", {}).get("codex", {})
    for table in (codex, *codex.get("variant", {}).values()):
        for key in OPT_OUTS:
            assert key not in table, f"{role.name} sets {key} on a Codex harness: {table}"

    completed = subprocess.run(
        [oneharness_bin, "config", "--config", str(role), "--format", "json"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    resolved = json.loads(completed.stdout)["harness"]["codex"]
    tables = {"codex": resolved, **resolved.get("variant", {})}
    for name, table in tables.items():
        assert table["app_server"]["value"] == "shared", (role.name, name, table["app_server"])
        assert table["shared_model_catalog"]["value"] is True, (role.name, name)
        assert table["analytics"]["value"] is False, (role.name, name)


@pytest.mark.parametrize(
    ("argv", "value"),
    [
        (["exec", "-c", "analytics.enabled=false"], "false"),
        (["exec", "--config", 'model_catalog_json="/x.json"'], "/x.json"),
        (["exec", "-c", "analytics.enabled=false", "-c", "analytics.enabled=true"], "true"),
        (["exec", "-c", "features.apps=false"], None),
    ],
    ids=["set", "quoted", "last-wins", "unset"],
)
def test_the_last_config_value_in_either_spelling_is_the_one_read(
    argv: list[str], value: str | None
) -> None:
    key = "model_catalog_json" if "model_catalog_json" in " ".join(argv) else "analytics.enabled"
    assert config_value(argv, key) == value
