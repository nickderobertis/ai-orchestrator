"""`orchestrator/unfinished.py`: both halves, every status, and its vocabulary's sources.

The recipe is driven end to end by `tests/unfinished/test_unfinished_e2e.py`; this tier runs
the module **in process**, because the coverage floor over `orchestrator/` is measured
in process. Nothing it stacks is doubled on the happy paths: the unwatched half asks the
`onepipeline` this checkout installs over run roots `tests/e2e/probe_run_root.py` builds,
and the unpublished half asks the real `onevcs` over a scratch registry of sessions opened
with the labels the engine stamps. The failure arms of the unwatched half are reached by
handing the module an engine that answers wrong — a published CLI doubled at the one
boundary the module spawns it through, which is where AGENTS.md allows a double — and the
unpublished half's by a registry the real `onevcs` refuses.

Three reconciliations sit here too, because each is a number this module restates:
`6` against the installed engine's own `unwatched` status over a built run root, `7`
against `scripts/unpublished.sh --print-surface`, and the whole vocabulary against
`scripts/unfinished.sh --print-surface`.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
module stays in `orchestrator:test` for the reason `tests/test_unpublished.py` gives: that
tier's measurement is the one the 100% floor over `orchestrator/` is read from, and the
recipe half is split out into the `unfinished` project.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import NamedTuple

import probe_run_root
import pytest
from unpublished_registry import Registry, Session, seeded
from waits import timeout as e2e_timeout

from orchestrator import unfinished, unpublished
from orchestrator.root import REPO_ROOT
from orchestrator.unfinished import BOTH, NOTHING_OWED, UNANSWERED, UNPUBLISHED, UNWATCHED

ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"
UNPUBLISHED_SH = REPO_ROOT / "scripts" / "unpublished.sh"
UNFINISHED_SH = REPO_ROOT / "scripts" / "unfinished.sh"

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
    return {
        unpublished.LABEL_RUN: run,
        unpublished.LABEL_NODE: "node",
        unpublished.LABEL_LAUNCHER: launcher,
    }


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
    monkeypatch.delenv(unpublished.LAUNCHER_SESSION_ENV, raising=False)
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
        assert mine.branch in published
        assert f"just unpublished --acknowledge {mine.branch}" in published, (
            "a counted row names acknowledging with a reason as its way out"
        )
    else:
        assert "no preserved unpublished branch for this target" in published


def test_the_session_defaults_to_the_launcher_the_environment_names(
    at_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(unpublished.LAUNCHER_SESSION_ENV, OWES_BRANCH)
    status, _, err = _main()
    assert status == UNPUBLISHED, err
    status, _, err = _main("--session", OWES_WATCH)
    assert status == UNWATCHED, "`--session` wins over the environment, for both halves"


def test_json_is_one_object_holding_both_halves(at_world: World) -> None:
    status, out, err = _main("--session", OWES_BOTH, "--json")
    assert status == BOTH, err
    document = json.loads(out)
    assert set(document) == {"unwatched", "unpublished"}
    assert any("run-both" in line for line in document["unwatched"])
    assert [row["branch"] for row in document["unpublished"]] == [at_world.both.branch]
    row = document["unpublished"][0]
    assert (row["run"], row["manager_session"], row["counted"]) == ("run-both", OWES_BOTH, True)


def test_an_acknowledged_branch_is_owed_no_longer_and_the_run_still_is(
    at_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(unpublished.LAUNCHER_SESSION_ENV, OWES_BOTH)
    out, err = io.StringIO(), io.StringIO()
    unpublished.main(
        ["--acknowledge", at_world.both.branch, "--reason", "asking the user"], out=out, err=err
    )
    assert "acknowledged" in out.getvalue(), err.getvalue()
    status, out_text, err_text = _main("--session", OWES_BOTH)
    assert status == UNWATCHED, err_text
    assert "acknowledged: asking the user" in out_text


def test_a_session_nothing_names_or_of_another_shape_is_refused(
    at_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    status, out, err = _main()
    assert status == unfinished.REFUSED and out == ""
    assert "no manager session identifies this shell" in err
    # An explicit empty `--session` is refused as given, never replaced by the environment.
    monkeypatch.setenv(unpublished.LAUNCHER_SESSION_ENV, OWES_BRANCH)
    status, out, err = _main("--session", "")
    assert status == unfinished.REFUSED and out == ""
    assert "no manager session identifies this shell" in err
    status, out, err = _main("--session", "not a session id")
    assert status == unfinished.REFUSED and out == ""
    assert "not the shape a session id has" in err


def test_print_surface_states_the_vocabulary_and_answers_on_its_own() -> None:
    status, out, _ = _main("--print-surface")
    assert status == NOTHING_OWED
    assert out.splitlines() == [
        "status 0 nothing-owed",
        "status 6 unwatched",
        "status 7 unpublished",
        "status 8 both",
        "status 2 refused",
        "status 1 unanswered",
    ]
    status, out, err = _main("--print-surface", "--json")
    assert status == unfinished.REFUSED and out == "" and "on its own" in err


def test_the_unpublished_half_failing_is_unanswered_never_a_count(
    at_world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A registry `onevcs` refuses: the status says a half could not answer, and which."""
    home = tmp_path / "broken-home"
    home.mkdir()
    (home / "registry.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    status, out, err = _main("--session", OWES_WATCH)
    assert status == UNANSWERED
    assert "the unpublished half could not answer" in err
    assert "run-watch" in out and unfinished.UNPUBLISHED_HEADING not in out, (
        "the half that answered is still shown, and the one that did not is not"
    )
    status, out, err = _main("--session", OWES_WATCH, "--json")
    assert status == UNANSWERED and out == "", "no object is emitted with a half missing"


def _engine_that(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> Path:
    """Hand the module an `onepipeline` that answers with ``script``, at its one boundary."""
    stand_in = tmp_path / "onepipeline"
    stand_in.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IXUSR)
    installed = unpublished._binary

    def binary(name: str) -> str | None:
        return str(stand_in) if name == "onepipeline" else installed(name)

    monkeypatch.setattr(unpublished, "_binary", binary)
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
    monkeypatch.setattr(unfinished, "UNWATCHED_TIMEOUT_SECONDS", 1)
    status, out, err = _main("--session", OWES_BRANCH)
    assert status == UNANSWERED, err
    assert "the unwatched half could not answer" in err and said in err
    assert unfinished.UNWATCHED_HEADING not in out and at_world.branch.branch in out
    if "noise" in script:
        assert "engine noise" in err, "the engine's own standard error is passed through"


def test_an_engine_that_is_missing_or_will_not_start_is_unanswered(
    at_world: World, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stand_in = _engine_that(tmp_path, monkeypatch, "exit 0")
    stand_in.chmod(0o644)
    status, _, err = _main("--session", OWES_NOTHING)
    assert status == UNANSWERED and "could not run" in err
    monkeypatch.setattr(unpublished, "_binary", lambda name: None)
    status, _, err = _main("--session", OWES_NOTHING)
    assert status == UNANSWERED and "found no `onepipeline`" in err


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


def _surface(script: Path) -> dict[str, int]:
    printed = subprocess.run(
        [str(script), "--print-surface"],
        capture_output=True,
        text=True,
        check=True,
        timeout=e2e_timeout(60),
    )
    statuses = [line.split() for line in printed.stdout.splitlines() if line.startswith("status ")]
    return {name: int(code) for _, code, name in statuses}


def test_the_unpublished_status_is_the_views_own_counted_status() -> None:
    assert _surface(UNPUBLISHED_SH)["counted"] == UNPUBLISHED


def test_the_wrapper_prints_the_modules_vocabulary() -> None:
    assert _surface(UNFINISHED_SH) == {
        status.name: status.code for status in unfinished.EXIT_STATUSES
    }
