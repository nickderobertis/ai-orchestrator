"""`orchestrator/unfinished.py`: both halves, every status, and its vocabulary's sources.

The recipe is driven end to end by `tests/unfinished/test_unfinished_e2e.py`; this tier runs
the module **in process**, because the coverage floor over `orchestrator/` is measured
in process. Nothing it stacks is doubled on the happy paths: the unwatched half asks the
`onepipeline` this checkout installs over run roots `tests/e2e/probe_run_root.py` builds,
and the unpublished half asks the same engine's `unpublished` over a scratch registry of
sessions opened with the labels the engine stamps. The failure arms are reached by handing
the module an engine that answers wrong — a published CLI doubled at the one boundary the
module spawns it through, which is where AGENTS.md allows a double — and by a registry the
real engine cannot read.

Two reconciliations sit here too, because each is a number or a path this module restates:
`6` against the installed engine's own `unwatched` status over a built run root (the `7`
is held to the engine's declared contract by `tests/test_engine_contracts.py`), and the
acknowledgement directory and drafter graph against the `Stop` hook and the recipe that
name them too.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
module stays in `orchestrator:test`: that tier's measurement is the one the 100% floor over
`orchestrator/` is read from, and the recipe half is split out into the `unfinished` project.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import NamedTuple

import probe_run_root
import pytest
from unpublished_registry import Registry, Session, seeded
from waits import timeout as e2e_timeout

from orchestrator import npm_installs, unfinished
from orchestrator.root import REPO_ROOT
from orchestrator.unfinished import BOTH, NOTHING_OWED, UNANSWERED, UNPUBLISHED, UNWATCHED

ENGINE = npm_installs.ENGINE

#: One manager session per status: a run unwatched and a branch counted, a branch
#: alone, a run alone, and nothing.
OWES_BOTH = "manager-owes-both"
OWES_BRANCH = "manager-owes-a-branch"
OWES_WATCH = "manager-owes-a-watch"
OWES_NOTHING = "manager-owes-nothing"


class World(NamedTuple):
    """A scratch registry and runs root, each holding what one manager session owes."""

    registry: Registry
    runs: Path
    both: Session
    branch: Session


def _labels(run: str, launcher: str) -> dict[str, str]:
    return {"run": run, "node": "node", "launcher": launcher}


def _unwatched_run(runs: Path, run: str, session: str) -> None:
    """A run of ``session`` the engine reads as undriven, unsettled and unwatched."""
    directory = probe_run_root.run_root(runs, run, session=session)
    (directory / "dispatches").mkdir(exist_ok=True)


def _current(runs: Path) -> None:
    """Have the engine fold and store every run's summary, which `unwatched` decides from."""
    listed = subprocess.run(
        [str(ENGINE), "runs"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "ONEPIPELINE_RUNS_DIR": str(runs)},
        cwd=runs,
        timeout=e2e_timeout(120),
    )
    assert listed.returncode == 0, listed.stderr


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    if not ENGINE.is_file():
        pytest.skip(f"this checkout has no installed engine at {ENGINE}")
    root = tmp_path_factory.mktemp("unfinished")
    registry = seeded(root / "registry")
    both = registry.open_session(labels=_labels("run-both", OWES_BOTH))
    registry.close_session(both)
    branch = registry.open_session(labels=_labels("run-branch", OWES_BRANCH))
    registry.close_session(branch)
    runs = root / "runs"
    runs.mkdir()
    _unwatched_run(runs, "run-both", OWES_BOTH)
    _unwatched_run(runs, "run-watch", OWES_WATCH)
    _current(runs)
    return World(registry, runs, both, branch)


@pytest.fixture
def at_world(world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> World:
    """Point this process at the world, with no launcher session of its own."""
    monkeypatch.setenv("ONEVCS_HOME", str(world.registry.home))
    monkeypatch.setenv("ONEPIPELINE_RUNS_DIR", str(world.runs))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv(unfinished.LAUNCHER_SESSION_ENV, raising=False)
    return world


def _main(*arguments: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    status = unfinished.main(list(arguments), out=out, err=err)
    return status, out.getvalue(), err.getvalue()


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        (OWES_BOTH, BOTH),
        (OWES_BRANCH, UNPUBLISHED),
        (OWES_WATCH, UNWATCHED),
        (OWES_NOTHING, NOTHING_OWED),
    ],
)
def test_each_status_is_what_the_two_halves_find_for_the_session(
    at_world: World, session: str, expected: int
) -> None:
    status, out, err = _main("--session", session)
    assert status == expected, f"{session}: {status}\n{out}\n{err}"
    assert unfinished.UNWATCHED_HEADING in out and unfinished.UNPUBLISHED_HEADING in out
    watched, published = out.split(unfinished.UNPUBLISHED_HEADING)
    if expected in (UNWATCHED, BOTH):
        assert f"run-{'both' if session == OWES_BOTH else 'watch'}" in watched
    else:
        assert unfinished.EVERY_RUN_WATCHED in watched
    if expected in (UNPUBLISHED, BOTH):
        mine = at_world.both if session == OWES_BOTH else at_world.branch
        assert f"{mine.branch} [{at_world.registry.identity}] counted; land it: " in published
        assert f"--pr-author-graph {unfinished.PR_AUTHOR_GRAPH}" in published, (
            "the landing the engine printed runs this host's drafter"
        )
        assert "just unpublished --acknowledge <branch>" in published
    else:
        assert "no preserved branch of this session's runs" in published


def test_the_session_defaults_to_the_launcher_the_environment_names(
    at_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(unfinished.LAUNCHER_SESSION_ENV, OWES_BRANCH)
    status, _, err = _main()
    assert status == UNPUBLISHED, err
    status, _, err = _main("--session", OWES_WATCH)
    assert status == UNWATCHED, "`--session` wins over the environment, for both halves"


def test_json_is_one_object_holding_the_unwatched_lines_and_the_engines_document(
    at_world: World,
) -> None:
    status, out, err = _main("--session", OWES_BOTH, "--json")
    assert status == BOTH, err
    document = json.loads(out)
    assert set(document) == {"unwatched", "unpublished"}
    assert any("run-both" in line for line in document["unwatched"])
    unpublished = document["unpublished"]
    assert set(unpublished) == {"target", "verdict", "rows", "unresolved"}
    assert unpublished["verdict"] == "owed"
    [row] = unpublished["rows"]
    assert (row["branch"], row["run"], row["manager_session"], row["counted"]) == (
        at_world.both.branch,
        "run-both",
        OWES_BOTH,
        True,
    )


def _acknowledge(world: World, state: Path, session: str, branch: str) -> None:
    """Acknowledge ``branch`` for ``session`` through the real recipe a manager runs."""
    done = subprocess.run(
        ["just", "unpublished", "--acknowledge", branch, "--reason", "asking the user"],
        cwd=REPO_ROOT,
        env={
            **world.registry.environment,
            "XDG_STATE_HOME": str(state),
            "ONEPIPELINE_LAUNCHER_SESSION": session,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=e2e_timeout(120),
    )
    assert done.returncode in (NOTHING_OWED, UNPUBLISHED), done.stderr


def test_an_acknowledged_branch_is_owed_no_longer_and_the_run_still_is(
    at_world: World, tmp_path: Path
) -> None:
    """The recipe records where this module reads: the directory both name is one."""
    _acknowledge(at_world, tmp_path, OWES_BOTH, at_world.both.branch)
    status, out, err = _main("--session", OWES_BOTH)
    assert status == UNWATCHED, err
    mine = f"{at_world.both.branch} [{at_world.registry.identity}]"
    assert f"{mine} acknowledged: asking the user" in out
    assert "just unpublished --acknowledge <branch>" not in out


def test_the_state_home_falls_back_as_the_shell_does(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "")
    fallback = Path.home() / ".local" / "state" / "ai-orchestrator" / "stop-unfinished"
    assert unfinished.acknowledgements() == fallback / "acknowledged"


def test_a_session_nothing_names_or_of_another_shape_is_refused(
    at_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, out, err = _main()
    assert status == unfinished.REFUSED and out == ""
    assert "no manager session identifies this shell" in err
    # An explicit empty `--session` is refused as given, never replaced by the environment.
    monkeypatch.setenv(unfinished.LAUNCHER_SESSION_ENV, OWES_BRANCH)
    status, out, err = _main("--session", "")
    assert status == unfinished.REFUSED and out == ""
    assert "no manager session identifies this shell" in err
    status, out, err = _main("--session", "not a session id")
    assert status == unfinished.REFUSED and out == ""
    assert "not the shape a session id has" in err


def test_the_unpublished_half_failing_is_unanswered_never_a_count(
    at_world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A registry the engine cannot read: the status says a half could not answer, and which."""
    home = tmp_path / "broken-home"
    home.mkdir()
    (home / "registry.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    status, out, err = _main("--session", OWES_WATCH)
    assert status == UNANSWERED
    assert "the unpublished half could not answer" in err and "exited 1" in err
    assert "run-watch" in out and unfinished.UNPUBLISHED_HEADING not in out, (
        "the half that answered is still shown, and the one that did not is not"
    )
    status, out, err = _main("--session", OWES_WATCH, "--json")
    assert status == UNANSWERED and out == "", "no object is emitted with a half missing"


def _engine_that(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unwatched: str, unpublished: str = ""
) -> Path:
    """Hand the module an `onepipeline` answering each verb with a script, at its boundary.

    A verb given no script is the installed engine's own answer.
    """
    stand_in = tmp_path / "onepipeline"
    installed = f'exec {ENGINE} "$@"'
    stand_in.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"unwatched) {unwatched or installed} ;;\n"
        f"unpublished) {unpublished or installed} ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(npm_installs, "ENGINE", stand_in)
    return stand_in


@pytest.mark.parametrize(
    ("script", "said"),
    [
        ("echo 'engine noise' >&2; exit 3", "a status outside its vocabulary"),
        ("exit 6", "naming no run"),
        ("exec sleep 30", "ran past its"),
    ],
)
def test_an_engine_answering_outside_its_vocabulary_is_unanswered(
    at_world: World,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: str,
    said: str,
) -> None:
    _engine_that(tmp_path, monkeypatch, script)
    monkeypatch.setattr(unfinished, "HALF_TIMEOUT_SECONDS", 5)
    status, out, err = _main("--session", OWES_BRANCH)
    assert status == UNANSWERED, err
    assert "the unwatched half could not answer" in err and said in err
    assert unfinished.UNWATCHED_HEADING not in out and at_world.branch.branch in out
    if "noise" in script:
        assert "engine noise" in err, "the engine's own standard error is passed through"


@pytest.mark.parametrize(
    ("document", "said"),
    [
        pytest.param("", "without the one JSON document it writes", id="nothing"),
        pytest.param("{}", "no `rows` list", id="no-rows"),
        pytest.param('{"rows": [5]}', "a row it could not read: 5", id="a-row-not-an-object"),
        pytest.param(
            '{"rows": [{"branch": "b", "identity": "i", "counted": "yes", "in_flight": false, '
            '"land_command": [], "acknowledgement": null}]}',
            "a row it could not read",
            id="a-flag-not-a-boolean",
        ),
        pytest.param(
            '{"rows": [{"branch": "b", "identity": "i", "counted": true, "in_flight": false, '
            '"land_command": [1], "acknowledgement": null}]}',
            "a row it could not read",
            id="a-command-word-not-a-string",
        ),
        pytest.param(
            '{"rows": [{"branch": "b", "identity": "i", "counted": false, "in_flight": false, '
            '"land_command": [], "acknowledgement": {"reason": 3}}]}',
            "an acknowledgement it could not read",
            id="an-unreadable-acknowledgement",
        ),
        pytest.param(
            '{"rows": [{"branch": "b", "identity": "i", "counted": false, "in_flight": false, '
            '"land_command": [], "acknowledgement": null}]}',
            "neither counted, in flight nor acknowledged",
            id="an-uncounted-row-with-no-reason",
        ),
    ],
)
def test_an_unpublished_document_that_cannot_be_read_is_unanswered(
    at_world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, document: str, said: str
) -> None:
    _engine_that(tmp_path, monkeypatch, "", f"printf '%s' '{document}'; exit 7")
    status, out, err = _main("--session", OWES_WATCH)
    assert status == UNANSWERED, err
    assert "the unpublished half could not answer" in err and said in err
    assert unfinished.UNPUBLISHED_HEADING not in out


def test_each_row_the_engine_writes_is_one_line_of_what_it_decided(
    at_world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A counted row with and without a landing, an in-flight row, an acknowledged one."""

    def row(branch: str, **fields: object) -> dict[str, object]:
        base: dict[str, object] = {"branch": branch, "identity": "i", "counted": False}
        return {**base, "in_flight": False, "land_command": [], "acknowledgement": None, **fields}

    rows = [
        row("a", counted=True, land_command=["onevcs", "integrate"]),
        row("b", counted=True),
        row("c", in_flight=True),
        row("d", acknowledgement={"reason": "kept"}),
    ]
    document = json.dumps({"rows": rows})
    _engine_that(tmp_path, monkeypatch, "", f"printf '%s' '{document}'; exit 7")
    status, out, err = _main("--session", OWES_WATCH)
    assert status == BOTH, err
    _, published = out.split(unfinished.UNPUBLISHED_HEADING)
    assert published.splitlines()[1:5] == [
        "a [i] counted; land it: onevcs integrate",
        "b [i] counted",
        "c [i] in flight",
        "d [i] acknowledged: kept",
    ]


def test_an_engine_that_is_missing_or_will_not_start_is_unanswered(
    at_world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Installed and executable, but naming an interpreter the kernel cannot find.
    stand_in = _engine_that(tmp_path, monkeypatch, "exit 0")
    stand_in.write_text("#!/no/such/interpreter\n", encoding="utf-8")
    status, _, err = _main("--session", OWES_NOTHING)
    assert status == UNANSWERED and "could not run" in err
    monkeypatch.setattr(npm_installs, "ENGINE", tmp_path / "no-engine-installed")
    monkeypatch.setenv("PATH", str(tmp_path / "an-empty-search-path"))
    status, _, err = _main("--session", OWES_NOTHING)
    assert status == UNANSWERED and "found no `onepipeline`" in err


def test_the_installed_engine_is_found_beside_this_checkout() -> None:
    assert unfinished._engine() == str(ENGINE)


def test_an_engine_on_the_search_path_never_answers_for_the_installed_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    on_path = tmp_path / "bin"
    on_path.mkdir()
    (on_path / "onepipeline").symlink_to(ENGINE)
    monkeypatch.setenv("PATH", str(on_path))
    assert unfinished._engine() == str(ENGINE)
    monkeypatch.setattr(npm_installs, "ENGINE", tmp_path / "no-engine-installed")
    with pytest.raises(unfinished.Unanswered, match="found no `onepipeline` to ask at"):
        unfinished._engine()


def test_the_unwatched_status_is_the_installed_engines_own(world: World) -> None:
    """`6` is the engine's word for that half, read off the engine over a built run root."""
    asked = subprocess.run(
        [str(ENGINE), "unwatched", "--session", OWES_WATCH],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "ONEPIPELINE_RUNS_DIR": str(world.runs)},
        cwd=world.runs,
        timeout=e2e_timeout(60),
    )
    assert asked.returncode == UNWATCHED, (
        f"the installed engine answers {asked.returncode} for a run nothing watches, and "
        f"orchestrator/unfinished.py spells that half {UNWATCHED}\n{asked.stderr}"
    )
    assert "run-watch" in asked.stdout


def test_the_hook_the_recipe_and_this_module_name_one_directory_and_one_drafter() -> None:
    """Three callers hand the engine this host's acknowledgements and drafter graph, so an
    acknowledgement recorded through one stands for the others."""
    directory = (
        '"${XDG_STATE_HOME:-$HOME/.local/state}/'
        + "/".join(unfinished.ACKNOWLEDGEMENTS_UNDER)
        + '"'
    )
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    [hook] = [hook["command"] for entry in settings["hooks"]["Stop"] for hook in entry["hooks"]]
    assert f"--unpublished-acknowledgements {directory}" in hook, hook
    assert '--unpublished-pr-author-graph "$CLAUDE_PROJECT_DIR/graphs/pr-author.yaml"' in hook
    justfile = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
    assert f"unpublished --acknowledgements {directory}" in justfile
    assert '--pr-author-graph "{{ repo_root }}/graphs/pr-author.yaml"' in justfile
    assert unfinished.PR_AUTHOR_GRAPH == REPO_ROOT / "graphs" / "pr-author.yaml"


def test_the_session_grammar_is_the_identity_helpers_own() -> None:
    """The shape this module refuses a session id of is the one `scripts/launcher-session.sh`
    judges with, read off the helper's own `launcher_session_is_usable`."""
    helper = (REPO_ROOT / "scripts" / "launcher-session.sh").read_text(encoding="utf-8")
    [pattern] = re.findall(r'\[\[ "\$1" =~ \^(.+)\$ \]\]', helper)
    assert unfinished.SESSION_ID.pattern == pattern
