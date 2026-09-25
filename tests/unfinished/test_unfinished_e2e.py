"""`just unfinished` answers what a manager session still owes, through the real recipe.

A manager's turn should not end with a run nothing is watching, and it should not end
with a branch its runs left preserved that nobody landed or deliberately left. The first
half is the installed engine's `onepipeline unwatched`; the second is `just unpublished`'s
own-sessions target, one filtered read of the session labels the engine stamps. This
recipe stacks the two for one manager session and answers one status. These journeys drive
it as an operator does — `just unfinished` as a subprocess — over a scratch `onevcs`
registry of **real labelled sessions** (`session open --label run=… --label node=…
--label launcher=…`, which is what the engine passes for every session a node opens) and
a runs root of run records `tests/e2e/probe_run_root.py` builds, which
`tests/test_engine_contracts.py` holds field by field to the installed engine.

Nothing is doubled on these paths: the recipe, the wrapper, the interpreter, the engine,
`onevcs`, git and the sessions are real. What makes it safe is isolation — `ONEVCS_HOME`,
`ONEPIPELINE_RUNS_DIR` and `XDG_STATE_HOME` each point at a scratch tree — so nothing this
host has registered, launched or acknowledged is read or moved.

The `Stop` hook is not driven here. The one `.claude/settings.json` registers is the
engine's own `onepipeline stop-guard`, a verdict over `unwatched` alone, and
`tests/unwatched/test_unwatched_and_stop_hook_e2e.py` drives it; the unpublished half
reaches a turn's end only once that verb can consult a second verdict.

These journeys are the `unfinished` project's, behind an Nx edge of their own: each spends
real `onevcs` sessions, real `git` and a real `just` per assertion, so `unfinishedWorkspace`
names the files they read and no tree, and a change to another tier does not run them.

llmlint: ignore-file[tests_mirror_real_usage] One state no interface produces is arranged:
a session held by a live owner. `onevcs session open` records the pid of a process that
has exited by the time it prints, so a live session exists only while the engine holds it,
which this suite may not run; `tests/unpublished_registry.py`'s `hold` points the record's
owner at a process the journey controls, the same two fields `onevcs` reads.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import probe_run_root
import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from unpublished_registry import Registry, Session, commit_on, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The seeding reaches `onevcs` through `uv run`, which waits on this checkout's exclusive
#: project-environment lock, so these journeys share the group that names it.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

ONEPIPELINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"
ONEVCS = REPO_ROOT / ".venv" / "bin" / "onevcs"

#: The statuses the recipe answers, as `scripts/unfinished.sh --print-surface` prints them;
#: `test_the_statuses_these_journeys_expect_are_the_ones_the_wrapper_prints` holds them.
NOTHING_OWED, UNWATCHED, UNPUBLISHED, BOTH, UNANSWERED = 0, 6, 7, 8, 1

#: One manager session per thing owed, so each journey reads its own answer.
OWES_A_BRANCH = "manager-owes-a-branch"
OWES_BOTH = "manager-owes-both"
OWES_A_WATCH = "manager-owes-a-watch"
ACKNOWLEDGES = "manager-acknowledges"
IN_FLIGHT_ONLY = "manager-in-flight-only"
OTHER_MANAGER = "manager-somebody-else"
WORKER = "worker-owning-nothing"

#: Every name `scripts/launcher-session.sh` reads an identity from or exports one as, read
#: off the helper itself and cleared, so the harness running this suite cannot stand in for
#: the session a journey names.
IDENTITY_NAMES = frozenset(
    name
    for pair in re.findall(
        r"\$\{([A-Z_]+):-|export ([A-Z_]+)=",
        (REPO_ROOT / "scripts" / "launcher-session.sh").read_text(encoding="utf-8"),
    )
    for name in pair
    if name
)

#: The bound the own-sessions read is held to at scale: the stop guard's verb bound, which
#: a turn-ending read of this view would have to fit inside.
OWN_READ_BOUND_SECONDS = 10.0


class World(NamedTuple):
    """One scratch registry and runs root, and the session each manager's runs opened."""

    registry: Registry
    runs: Path
    branch: Session
    both: Session
    acknowledged: Session
    held: Session
    other: Session
    owner: subprocess.Popen[bytes]


def _labels(run: str, launcher: str) -> dict[str, str]:
    return {"run": run, "node": "node-1", "launcher": launcher}


def _closed(registry: Registry, run: str, launcher: str) -> Session:
    session = registry.open_session(labels=_labels(run, launcher))
    registry.close_session(session)
    return session


def _unwatched_run(runs: Path, run: str, session: str) -> None:
    """A run of ``session`` nothing drives, nothing settled and nothing watches."""
    directory = probe_run_root.run_root(runs, run, session=session)
    (directory / "dispatches").mkdir(exist_ok=True)
    listed = subprocess.run(  # noqa: S603 - the installed engine, named by absolute path
        [str(ONEPIPELINE), "runs"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "ONEPIPELINE_RUNS_DIR": str(runs)},
        cwd=runs,
        timeout=e2e_timeout(120),
    )
    assert listed.returncode == 0, f"the engine could not fold this runs root:\n{listed.stderr}"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    if not ONEPIPELINE.is_file() or shutil.which("just") is None:
        pytest.skip("this checkout has no installed engine, or no just, to drive")
    root = tmp_path_factory.mktemp("unfinished-e2e")
    registry = seeded(root / "registry")
    branch = _closed(registry, "run-branch", OWES_A_BRANCH)
    both = _closed(registry, "run-both", OWES_BOTH)
    acknowledged = _closed(registry, "run-ack", ACKNOWLEDGES)
    other = _closed(registry, "run-other", OTHER_MANAGER)
    held = registry.open_session(labels=_labels("run-held", IN_FLIGHT_ONLY))
    owner = registry.hold(held)
    runs = root / "runs"
    runs.mkdir()
    _unwatched_run(runs, "run-both", OWES_BOTH)
    _unwatched_run(runs, "run-watch", OWES_A_WATCH)
    try:
        yield World(registry, runs, branch, both, acknowledged, held, other, owner)
    finally:
        owner.kill()
        owner.wait()


class Answer(NamedTuple):
    status: int
    stdout: str
    stderr: str

    def halves(self) -> tuple[str, str]:
        """The unwatched block and the unpublished block, split at the second heading."""
        heading = "== unpublished:"
        assert self.stdout.startswith("== unwatched:"), self.stdout
        assert heading in self.stdout, self.stdout
        watched, published = self.stdout.split(heading, 1)
        return watched, published

    # `Any` because the object is the recipe's own JSON read back as parsed: its halves are
    # lists of strings and of the view's rows, which the tests index by the names the row
    # contract declares rather than through a second model of that shape.
    def document(self) -> dict[str, Any]:
        parsed = json.loads(self.stdout)
        assert isinstance(parsed, dict), self.stdout
        return parsed


def _environment(world: World, state: Path, launcher: str | None) -> dict[str, str]:
    environment = {k: v for k, v in world.registry.environment.items() if k not in IDENTITY_NAMES}
    environment["ONEPIPELINE_RUNS_DIR"] = str(world.runs)
    environment["XDG_STATE_HOME"] = str(state)
    if launcher is not None:
        environment["ONEPIPELINE_LAUNCHER"] = "claude-code"
        environment["ONEPIPELINE_LAUNCHER_SESSION"] = launcher
    return environment


def _just(
    world: World,
    recipe: str,
    *arguments: str,
    state: Path,
    launcher: str | None = None,
    **overrides: str,
) -> Answer:
    done = subprocess.run(  # noqa: S603 - the real recipe, run as an operator runs it
        ["just", recipe, *arguments],
        cwd=REPO_ROOT,
        env={**_environment(world, state, launcher), **overrides},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    return Answer(done.returncode, done.stdout, done.stderr)


def test_the_statuses_these_journeys_expect_are_the_ones_the_wrapper_prints() -> None:
    printed = subprocess.run(  # noqa: S603 - the tracked wrapper
        [str(REPO_ROOT / "scripts" / "unfinished.sh"), "--print-surface"],
        capture_output=True,
        text=True,
        check=True,
        timeout=e2e_timeout(60),
    ).stdout.splitlines()
    statuses = {name: int(code) for _, code, name in (line.split() for line in printed)}
    assert {
        "nothing-owed": NOTHING_OWED,
        "unwatched": UNWATCHED,
        "unpublished": UNPUBLISHED,
        "both": BOTH,
        "unanswered": UNANSWERED,
    }.items() <= statuses.items()


def test_a_preserved_branch_of_an_own_run_is_owed_with_both_ways_out(
    world: World, tmp_path: Path
) -> None:
    answer = _just(world, "unfinished", "--session", OWES_A_BRANCH, state=tmp_path)
    assert answer.status == UNPUBLISHED, answer
    watched, published = answer.halves()
    assert "no run this session launched is unwatched" in watched
    assert world.branch.branch in published
    assert world.other.branch not in published, "another manager's branch is never this one's"
    assert "land it:" in published and "just publish-branch" in published
    assert f"just unpublished --acknowledge {world.branch.branch}" in published
    assert '--reason "<why it is deliberately left>"' in published


def test_another_sessions_preserved_branch_is_not_owed(world: World, tmp_path: Path) -> None:
    """A manager whose runs opened no session answers nothing, whatever the host holds."""
    answer = _just(world, "unfinished", "--session", WORKER, state=tmp_path)
    assert answer.status == NOTHING_OWED, answer


def test_an_own_runs_branch_beside_an_unwatched_own_run_is_both(
    world: World, tmp_path: Path
) -> None:
    answer = _just(world, "unfinished", "--session", OWES_BOTH, state=tmp_path)
    assert answer.status == BOTH, answer
    watched, published = answer.halves()
    assert "run-both" in watched and world.both.branch in published


def test_an_unwatched_own_run_alone_is_the_engines_own_status(world: World, tmp_path: Path) -> None:
    answer = _just(world, "unfinished", "--session", OWES_A_WATCH, state=tmp_path)
    assert answer.status == UNWATCHED, answer
    watched, published = answer.halves()
    assert "run-watch" in watched
    assert "no preserved unpublished branch for this target" in published


def test_a_reasoned_acknowledgement_ends_it_and_a_new_commit_owes_it_again(
    world: World, tmp_path: Path
) -> None:
    before = _just(world, "unfinished", "--session", ACKNOWLEDGES, state=tmp_path)
    assert before.status == UNPUBLISHED, before

    receipt = _just(
        world,
        "unpublished",
        "--acknowledge",
        world.acknowledged.branch,
        "--reason",
        "asking the user whether to land it",
        state=tmp_path,
        launcher=ACKNOWLEDGES,
    )
    assert "acknowledged" in receipt.stdout, receipt
    after = _just(world, "unfinished", "--session", ACKNOWLEDGES, state=tmp_path)
    assert after.status == NOTHING_OWED, after
    assert "acknowledged: asking the user whether to land it" in after.stdout

    commit_on(world.registry.checkout, world.acknowledged.branch, "moved.txt")
    moved = _just(world, "unfinished", "--session", ACKNOWLEDGES, state=tmp_path)
    assert moved.status == UNPUBLISHED, "a branch that moved past its acknowledgement is owed"


def test_an_in_flight_session_is_never_owed(world: World, tmp_path: Path) -> None:
    answer = _just(world, "unfinished", "--session", IN_FLIGHT_ONLY, "--json", state=tmp_path)
    assert answer.status == NOTHING_OWED, answer
    rows = answer.document()["unpublished"]
    assert [row["branch"] for row in rows] == [world.held.branch]
    assert rows[0]["in_flight"] is True and rows[0]["counted"] is False


def test_a_worker_whose_own_session_owns_nothing_is_owed_nothing(
    world: World, tmp_path: Path
) -> None:
    """A dispatched worker inherits its manager's launcher session; asked about its own, nothing."""
    answer = _just(world, "unfinished", "--session", WORKER, state=tmp_path, launcher=OWES_BOTH)
    assert answer.status == NOTHING_OWED, answer


def test_the_session_defaults_to_the_one_the_harness_names(world: World, tmp_path: Path) -> None:
    answer = _just(world, "unfinished", state=tmp_path, launcher=OWES_A_BRANCH)
    assert answer.status == UNPUBLISHED, answer
    refused = _just(world, "unfinished", state=tmp_path)
    assert refused.status == 2 and "no manager session identifies this shell" in refused.stderr


def test_json_is_one_object_of_the_two_halves(world: World, tmp_path: Path) -> None:
    answer = _just(world, "unfinished", "--session", OWES_BOTH, "--json", state=tmp_path)
    assert answer.status == BOTH, answer
    document = answer.document()
    assert set(document) == {"unwatched", "unpublished"}
    assert any("run-both" in line for line in document["unwatched"])
    row = document["unpublished"][0]
    assert (row["branch"], row["run"], row["manager_session"]) == (
        world.both.branch,
        "run-both",
        OWES_BOTH,
    )


def test_the_unpublished_half_failing_is_never_nothing_owed(world: World, tmp_path: Path) -> None:
    broken = tmp_path / "broken-home"
    broken.mkdir()
    (broken / "registry.json").write_text("{not json", encoding="utf-8")
    answer = _just(
        world, "unfinished", "--session", OWES_A_WATCH, state=tmp_path, ONEVCS_HOME=str(broken)
    )
    assert answer.status == UNANSWERED, answer
    assert "the unpublished half could not answer" in answer.stderr
    assert "run-watch" in answer.stdout, "the half that answered is still shown"


#: The registry the timing journey reads: three identities, twenty closed session clones
#: each, ten preserved branches spread across them, three under the manager's label.
IDENTITIES = 3
CLOSED_PER_IDENTITY = 20
PRESERVED = 10
OWN_PRESERVED = 3
SCALE_MANAGER = "manager-at-scale"


def _onevcs(registry: Registry, *arguments: str) -> str:
    """The installed `onevcs`, directly: sixty sessions through `uv run` would be the cost."""
    done = registry.run(str(ONEVCS), *arguments)
    assert done.returncode == 0, f"onevcs {' '.join(arguments)}:\n{done.stderr}"
    return done.stdout


def test_the_own_sessions_read_fits_its_bound_at_this_hosts_scale(tmp_path: Path) -> None:
    if not ONEVCS.is_file() or shutil.which("just") is None:
        pytest.skip("this checkout has no installed onevcs, or no just, to drive")
    root = tmp_path / "scale"
    registries = [seeded(root, name=f"identity-{n}") for n in range(IDENTITIES)]
    preserved = 0
    own: list[str] = []
    for index in range(IDENTITIES * CLOSED_PER_IDENTITY):
        registry = registries[index % IDENTITIES]
        launcher = SCALE_MANAGER if preserved < OWN_PRESERVED else "manager-else"
        opened = json.loads(
            _onevcs(
                registry,
                "session",
                "open",
                str(registry.checkout),
                "--label",
                f"run=run-{index}",
                "--label",
                "node=node",
                "--label",
                f"launcher={launcher}",
            )
        )
        if preserved < PRESERVED:
            worktree = Path(opened["worktree"])
            (worktree / "work.txt").write_text(f"{index}\n", encoding="utf-8")
            for step in (["add", "-A"], ["commit", "-q", "-m", f"feat: work {index}"]):
                subprocess.run(  # noqa: S603 - git in the session's own worktree
                    ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *step],
                    cwd=worktree,
                    check=True,
                    capture_output=True,
                    timeout=e2e_timeout(60),
                )
            if launcher == SCALE_MANAGER:
                own.append(opened["branch"])
            preserved += 1
        _onevcs(registry, "session", "close", opened["token"])

    environment = {k: v for k, v in registries[0].environment.items() if k not in IDENTITY_NAMES}
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    started = time.monotonic()
    done = subprocess.run(  # noqa: S603 - the real recipe, run as an operator runs it
        ["just", "unpublished", "--own", "--session", SCALE_MANAGER, "--no-disk", "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    elapsed = time.monotonic() - started
    # Said on standard output, so a run with `-s` or a failing one states the measurement.
    print(f"own-sessions read over the scale registry: {elapsed:.2f}s")
    assert done.returncode == UNPUBLISHED, f"{done.stdout}\n{done.stderr}"
    assert sorted(row["branch"] for row in json.loads(done.stdout)) == sorted(own)
    assert elapsed < OWN_READ_BOUND_SECONDS, (
        f"`just unpublished --own --no-disk` took {elapsed:.1f}s over {IDENTITIES} identities, "
        f"{IDENTITIES * CLOSED_PER_IDENTITY} closed sessions and {PRESERVED} preserved "
        f"branches; its bound is {OWN_READ_BOUND_SECONDS:.0f}s"
    )


def _scratch_checkout(root: Path, helper: str | None = None) -> Path:
    """A checkout holding the real justfile, both wrappers, the helper and the two modules,
    and no `.venv`.

    With no `.venv/bin` beside the module, the interpreter is the `python3` on the search
    path and the engine the `onepipeline` there — the one place a journey can hand it an
    engine that answers wrong. ``helper`` replaces the identity helper's text, and the
    empty string leaves it out.
    """
    (root / "scripts").mkdir(parents=True)
    (root / "orchestrator").mkdir()
    shutil.copy2(REPO_ROOT / "justfile", root / "justfile")
    for wrapper in ("unfinished.sh", "unpublished.sh"):
        shutil.copy2(REPO_ROOT / "scripts" / wrapper, root / "scripts" / wrapper)
    real = (REPO_ROOT / "scripts" / "launcher-session.sh").read_text(encoding="utf-8")
    # llmlint: ignore-block[e2e_not_mocked] Only the refusal journeys pass a ``helper``: a
    # checkout whose identity helper is missing or will not load is a corrupted checkout,
    # which no interface produces, and those journeys prove the wrapper names the file to
    # restore. Every other journey copies the real helper whole.
    if helper != "":
        (root / "scripts" / "launcher-session.sh").write_text(
            real if helper is None else helper, encoding="utf-8"
        )
    # llmlint: ignore-end[e2e_not_mocked]
    for module in ("unfinished.py", "unpublished.py", "root.py"):
        shutil.copy2(REPO_ROOT / "orchestrator" / module, root / "orchestrator" / module)
    return root / "scripts" / "unfinished.sh"


def _search_path(root: Path, *, engine: bool = True, python: bool = True) -> Path:
    """A search path of exactly the tools a run of the wrapper needs.

    The installed `onevcs` and `onepipeline` — the engine left out when ``engine`` is
    false — `python3` unless ``python`` is false, and what `just`, the wrappers and
    `onevcs` spawn.
    """
    tools = root / "bin"
    tools.mkdir(parents=True)
    (tools / "onevcs").symlink_to(ONEVCS)
    if engine:
        (tools / "onepipeline").symlink_to(ONEPIPELINE)
    for tool in (
        "bash",
        "dirname",
        "env",
        "git",
        "mkdir",
        "sh",
        "sleep",
        *(["python3"] if python else []),
    ):
        found = shutil.which(tool, path="/usr/bin:/bin")
        assert found is not None, tool
        (tools / tool).symlink_to(found)
    return tools


def _wrapper(
    world: World, tmp_path: Path, script: Path, path: Path
) -> subprocess.CompletedProcess[str]:
    environment = _environment(world, tmp_path / "state", None)
    environment["PATH"] = str(path)
    return subprocess.run(  # noqa: S603 - the tracked wrapper, copied whole
        [shutil.which("bash") or "/bin/bash", str(script), "--session", OWES_A_BRANCH],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )


# llmlint: ignore-block[e2e_not_mocked] The engine is a published CLI, which AGENTS.md's
# realistic-tests invariant doubles at the boundary a recipe delegates to: an engine exiting
# outside its vocabulary, naming no run, or running past its bound is a state the installed
# release never produces on demand, and this journey proves the wrapper's answer to each.
# The wrapper, the interpreter, both modules, `onevcs` and the registry are all real.
@pytest.mark.parametrize(
    ("engine", "said"),
    [
        pytest.param("echo 'engine noise' >&2; exit 3", "a status outside its vocabulary", id="3"),
        pytest.param("exit 6", "naming no run", id="6-naming-nothing"),
        pytest.param(None, "found no `onepipeline`", id="missing"),
        pytest.param("exec sleep 120", "ran past its 60s bound", id="past-its-bound"),
    ],
)
def test_an_engine_that_cannot_answer_is_never_nothing_owed(
    world: World, tmp_path: Path, engine: str | None, said: str
) -> None:
    """Through the real wrapper: the unwatched half fails, the status says so, and the
    unpublished half that did answer is still shown."""
    script = _scratch_checkout(tmp_path / "checkout")
    tools = _search_path(tmp_path, engine=False)
    if engine is not None:
        stand_in = tools / "onepipeline"
        stand_in.write_text(f"#!/bin/sh\n{engine}\n", encoding="utf-8")
        stand_in.chmod(0o755)
    done = _wrapper(world, tmp_path, script, tools)
    assert done.returncode == UNANSWERED, f"{done.stdout}\n{done.stderr}"
    assert "the unwatched half could not answer" in done.stderr and said in done.stderr
    assert "== unwatched:" not in done.stdout
    assert world.branch.branch in done.stdout, "the half that answered is still shown"


# llmlint: ignore-end[e2e_not_mocked]


def test_print_surface_through_the_recipe_answers_on_its_own(world: World, tmp_path: Path) -> None:
    answer = _just(world, "unfinished", "--print-surface", "--json", state=tmp_path)
    assert answer.status == 2 and answer.stdout == ""
    assert "`--print-surface` answers on its own" in answer.stderr


@pytest.mark.parametrize(
    ("helper", "refusal"),
    [
        pytest.param("", "required helper is not a readable regular file: ", id="missing"),
        # llmlint: ignore-block[e2e_not_mocked] Not a stand-in for a working helper: a
        # helper that will not load is the corrupted checkout this journey is about, as
        # `_scratch_checkout` says, and the wrapper reading it is the real one.
        pytest.param("false\n", "is readable but could not be loaded", id="fails-to-load"),
        # llmlint: ignore-end[e2e_not_mocked]
    ],
)
def test_the_wrapper_refuses_an_identity_helper_it_cannot_use(
    world: World, tmp_path: Path, helper: str, refusal: str
) -> None:
    script = _scratch_checkout(tmp_path / "checkout", helper)
    done = _wrapper(world, tmp_path, script, _search_path(tmp_path))
    assert done.returncode == UNANSWERED, done.stderr
    assert refusal in done.stderr and str(script.parent / "launcher-session.sh") in done.stderr
    assert done.stdout == ""


def test_the_wrapper_runs_under_the_system_interpreter_and_refuses_without_one(
    world: World, tmp_path: Path
) -> None:
    """With no `.venv`, `python3` off the search path runs the module; with none, a refusal."""
    script = _scratch_checkout(tmp_path / "checkout")
    found = _wrapper(world, tmp_path / "with", script, _search_path(tmp_path / "with"))
    assert found.returncode == UNPUBLISHED, found.stderr
    assert world.branch.branch in found.stdout

    path = _search_path(tmp_path / "without", python=False)
    missing = _wrapper(world, tmp_path / "without", script, path)
    assert missing.returncode == UNANSWERED and "found no python3" in missing.stderr


def test_the_wrapper_says_what_failed_when_its_interpreter_will_not_execute(
    world: World, tmp_path: Path
) -> None:
    """A `.venv` whose base interpreter was removed: named, with what to do about it."""
    script = _scratch_checkout(tmp_path / "checkout")
    interpreter = script.parent.parent / ".venv" / "bin" / "python3"
    # llmlint: ignore-block[e2e_not_mocked] This is the broken state under test, not a
    # stand-in for a working boundary: a `.venv` interpreter whose base was removed is
    # what `uv` leaves behind when its Python is uninstalled, and nothing a journey may run
    # produces one. The wrapper, its `exec` and its report are all real.
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(f"#!{tmp_path / 'removed' / 'python3'}\n", encoding="utf-8")
    interpreter.chmod(0o755)
    # llmlint: ignore-end[e2e_not_mocked]
    done = _wrapper(world, tmp_path, script, _search_path(tmp_path))
    assert done.returncode == UNANSWERED, done.stderr
    assert f"could not execute the interpreter {interpreter}" in done.stderr
    assert "just bootstrap" in done.stderr and done.stdout == ""


def test_the_wrapper_refuses_a_checkout_missing_the_module(world: World, tmp_path: Path) -> None:
    script = _scratch_checkout(tmp_path / "checkout")
    module = script.parent.parent / "orchestrator" / "unfinished.py"
    module.unlink()
    done = _wrapper(world, tmp_path, script, _search_path(tmp_path))
    assert done.returncode == UNANSWERED, done.stderr
    assert f"the view's module is not a readable regular file: {module}" in done.stderr
    assert done.stdout == ""


def test_a_malformed_session_id_is_refused_by_the_unfinished_recipe(
    world: World, tmp_path: Path
) -> None:
    answer = _just(world, "unfinished", "--session", "not a session!", state=tmp_path)
    assert answer.status == 2 and answer.stdout == "", answer
    assert "'not a session!' is not the shape a session id has" in answer.stderr


def test_a_malformed_manager_id_is_refused_by_the_own_sessions_read(
    world: World, tmp_path: Path
) -> None:
    answer = _just(
        world, "unpublished", "--own", "--session", "not a session!", "--no-disk", state=tmp_path
    )
    assert answer.status == 2 and answer.stdout == "", answer
    assert "'not a session!' is not the shape a session id has" in answer.stderr
    assert "nothing can carry it as a `launcher` label" in answer.stderr


# llmlint: ignore-block[e2e_not_mocked] `onevcs` is a published CLI, which AGENTS.md's
# realistic-tests invariant doubles at the boundary a recipe delegates to: a row naming a
# session that is not a token, a label that is not a string, or a session whose record holds
# another branch is a row the installed release does not write on demand, and these
# journeys prove the view's answer to each. The stand-in runs the installed binary for
# every verb and only lays the malformed field over its `recoverable` rows; `just`, the
# wrapper, the interpreter, the module and the registry are all real.
def _patched_onevcs(tools: Path, patch: dict[str, Any]) -> None:
    """Put an `onevcs` on ``tools`` that is the installed one, save ``patch`` on its rows.

    Every verb is the installed binary's answer; `recoverable`'s rows each have ``patch``
    laid over them, a dict value merged into the row's dict of that name.
    """
    stand_in = tools / "onevcs"
    stand_in.unlink()
    stand_in.write_text(
        f"#!{sys.executable}\n"
        "import json, subprocess, sys\n"
        f"done = subprocess.run([{str(ONEVCS)!r}, *sys.argv[1:]], capture_output=True, "
        "text=True)\n"
        "sys.stderr.write(done.stderr)\n"
        "if done.returncode != 0 or sys.argv[1:2] != ['recoverable']:\n"
        "    sys.stdout.write(done.stdout)\n"
        "    sys.exit(done.returncode)\n"
        f"patch = json.loads({json.dumps(patch)!r})\n"
        "rows = json.loads(done.stdout)\n"
        "for row in rows:\n"
        "    for key, value in patch.items():\n"
        "        if isinstance(value, dict) and isinstance(row.get(key), dict):\n"
        "            row[key].update(value)\n"
        "        else:\n"
        "            row[key] = value\n"
        "print(json.dumps(rows))\n",
        encoding="utf-8",
    )
    stand_in.chmod(0o755)


def _recipe_in(
    world: World, tmp_path: Path, checkout: Path, tools: Path, *arguments: str
) -> Answer:
    """`just` in the scratch ``checkout``, whose only tools are the ones on ``tools``."""
    environment = _environment(world, tmp_path / "state", None)
    environment["PATH"] = str(tools)
    done = subprocess.run(  # noqa: S603 - the real recipe, in a copy of this checkout
        [shutil.which("just") or "just", *arguments],
        cwd=checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    return Answer(done.returncode, done.stdout, done.stderr)


@pytest.mark.parametrize(
    ("patch", "said"),
    [
        pytest.param(
            {"session": "not-a-token"},
            'its session "not-a-token" is not a session token',
            id="malformed-token",
        ),
        pytest.param(
            {"labels": {"node": 5}},
            "its session labels could not be read whole and only the string ones are used",
            id="malformed-label",
        ),
    ],
)
def test_a_row_the_view_cannot_read_whole_is_said_and_still_owed(
    world: World, tmp_path: Path, patch: dict[str, Any], said: str
) -> None:
    script = _scratch_checkout(tmp_path / "checkout")
    tools = _search_path(tmp_path)
    _patched_onevcs(tools, patch)
    answer = _recipe_in(
        world,
        tmp_path,
        script.parent.parent,
        tools,
        "unpublished",
        "--own",
        "--session",
        OWES_A_BRANCH,
        "--no-disk",
        "--json",
    )
    assert answer.status == UNPUBLISHED, answer
    assert f"{world.branch.branch}: {said}" in answer.stderr
    [row] = json.loads(answer.stdout)
    assert (row["branch"], row["manager_session"], row["counted"]) == (
        world.branch.branch,
        OWES_A_BRANCH,
        True,
    )


def test_a_session_whose_record_holds_another_branch_is_not_the_rows_holder(
    world: World, tmp_path: Path
) -> None:
    """A row naming another session's token measures its own branch's run root, not that one's."""
    script = _scratch_checkout(tmp_path / "checkout")
    tools = _search_path(tmp_path)
    _patched_onevcs(tools, {"session": world.other.token})
    answer = _recipe_in(
        world,
        tmp_path,
        script.parent.parent,
        tools,
        "unpublished",
        "--own",
        "--session",
        OWES_A_BRANCH,
        "--json",
    )
    assert answer.status == UNPUBLISHED, answer
    [row] = json.loads(answer.stdout)
    assert (row["branch"], row["session"]) == (world.branch.branch, world.other.token)
    assert row["disk"]["run_root"] == str(world.branch.run_root), row["disk"]


# llmlint: ignore-end[e2e_not_mocked]
