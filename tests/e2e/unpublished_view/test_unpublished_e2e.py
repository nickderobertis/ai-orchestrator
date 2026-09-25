"""`just unpublished` answers for this host's preserved branches, through the real recipe.

The view exists because `just recoverable` run inside a checkout answers for that
identity alone, so a manager can miss branches of other identities — each pinning a
run root whose worktree still carries its build
output. What an operator and a later `Stop` hook both touch is the **recipe**: its
targets, its rows, its disk reading, its acknowledgement and its exit status. So this
journey drives `just unpublished` as a subprocess, the way both of them do, and asserts
what comes back.

`tests/test_unpublished.py` runs the same module in process, which is what the coverage
floor over `orchestrator/` is measured against; this module proves the half that one
cannot reach — that `just` reaches the wrapper, that the wrapper resolves an interpreter
and a `PYTHONPATH` under which the module imports, that the manager session
`scripts/launcher-session.sh` establishes is the one an acknowledgement is keyed on,
and that the status `just` returns is the module's own.

Everything is real — the recipe, the wrapper, the interpreter, `onevcs`, git and the
sessions. What makes it safe is isolation rather than substitution, exactly as
`tests/e2e/test_recoverable_resume_commands_e2e.py` is safe: `ONEVCS_HOME` points at a
scratch registry over a throwaway bare origin, so nothing this host has registered is
read or moved, and `XDG_STATE_HOME` points at a scratch tree, so no acknowledgement this
host holds is read or written.

The recipe is deliberately **not** a row of `tests/e2e/test_delegated_recipes_e2e.py`:
that table holds a wrapper that reaches one published verb with a promised argv, and this
one reaches a module of this repository which then asks `onevcs` several things and joins
the answers. There is no argv to promise, so the proof is what the view answers.

It lives in the `unpublished-view` project, behind an edge of its own: every test spends
real `onevcs` session verbs, real `git` and a real `just`, so `nx affected` should charge
that to a change of what the journey drives and nothing else. `unpublishedViewWorkspace`
names those files one by one, measured from what the tier opens, and `tests/conftest.py`
fails a test here that opens a path of this checkout the key does not name.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from unpublished_registry import ORPHAN_BRANCH, Registry, Session, commit_on, git, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT
from orchestrator.unpublished import (
    COUNTED,
    NOTHING_COUNTED,
    REFUSED,
    ROW_FIELDS,
    UNANSWERED,
)

#: The one worker this module runs on: the seeding below reaches `onevcs` through `uv run`,
#: which waits on this checkout's exclusive project-environment lock, and `--dist loadgroup`
#: would otherwise scatter these tests across four workers, seeding a whole registry on each.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The manager session every acknowledgement here is keyed on, and a second one that must
#: never see it: an acknowledgement is one session's record of what it deliberately left.
MANAGER = "manager-session-journey"
OTHER_MANAGER = "manager-session-other"

#: Every name `scripts/launcher-session.sh` reads an identity from. Cleared before each
#: invocation so the harness running this suite cannot key an acknowledgement instead of
#: the session the test names — the suite runs under a real claude-code or codex session,
#: whose id is exported into this process.
IDENTITY_NAMES = (
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
)

#: The helper those names are read by, and the wrapper that sources it; both are copied
#: whole into a scratch checkout to reach the wrapper's refusals.
LAUNCHER_SESSION_SH = REPO_ROOT / "scripts" / "launcher-session.sh"
UNPUBLISHED_SH = REPO_ROOT / "scripts" / "unpublished.sh"
MODULE = REPO_ROOT / "orchestrator" / "unpublished.py"
#: Every environment name the helper reads or exports, as bash spells each one.
HELPER_NAMES = re.compile(r"\$\{([A-Z_]+):-|export ([A-Z_]+)=")


class View(NamedTuple):
    """One `just unpublished` invocation, as a consumer of the recipe sees it."""

    status: int
    stdout: str
    stderr: str

    # `Any` because a row is the module's own JSON, read back as parsed: its values are
    # strings, booleans, nulls and nested objects by field, and the tests index into them
    # by the names `ROW_FIELDS` declares rather than through a second model of that shape.
    def rows(self) -> dict[str, dict[str, Any]]:
        """The `--json` rows by branch, which is what the row shape is read through."""
        parsed = json.loads(self.stdout)
        assert isinstance(parsed, list), f"--json did not emit an array: {self.stdout!r}"
        return {row["branch"]: row for row in parsed}


class Seeded(NamedTuple):
    """The registry with one branch in each state this view distinguishes."""

    registry: Registry
    #: A session that committed and closed: preserved, not in flight, counted.
    closed: Session
    #: A session whose run-root lease is held: shown, marked in flight, never counted.
    held: Session
    lease: subprocess.Popen[bytes]
    state: Path


class Scope(NamedTuple):
    """A registry and a state home: all an invocation of the recipe reads."""

    registry: Registry
    state: Path


@pytest.fixture(scope="module")
def seed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Seeded]:
    """One registry carrying every branch state, seeded once with the real verbs.

    The acknowledgement journeys each work under a state home of their own, and the
    journey that makes a branch count again commits on a branch of its own, so one
    seeding serves them all.
    """
    root = tmp_path_factory.mktemp("unpublished-e2e")
    registry = seeded(root)
    closed = registry.open_session()
    registry.close_session(closed)
    held = registry.open_session()
    # llmlint: ignore-block[tests_mirror_real_usage] The CLI that opens a session
    # exits before it can keep a lease held. This fixture takes the real advisory
    # lock of onevcs 0.30.1; the recipe journey below observes both held and released
    # states through onevcs, so a changed lock layout makes the test fail.
    lease = registry.hold_lease(held)
    # llmlint: ignore-end[tests_mirror_real_usage]
    # Build output under the live session's worktree, which is the worktree that still
    # exists: `tests/test_unpublished.py` records that a closed session's goes with it.
    # One directory the view calls out and one it does not, so the reading is shown to be
    # the declared tuple rather than everything under the worktree.
    (held.worktree / "node_modules").mkdir()
    (held.worktree / "node_modules" / "blob").write_bytes(b"\0" * 300_000)
    with (held.worktree / "node_modules" / "sparse").open("wb") as sparse:
        sparse.seek(64 * 1024 * 1024)
        sparse.write(b"x")
    (held.worktree / "scratch").mkdir()
    (held.worktree / "scratch" / "notes").write_bytes(b"\0" * 1_000)
    try:
        yield Seeded(registry, closed, held, lease, root / "state")
    finally:
        assert lease.stdin is not None
        lease.stdin.close()
        assert lease.wait(timeout=e2e_timeout(30)) == 0


def _unpublished(
    seed: Seeded | Scope,
    *arguments: str,
    state: Path | None = None,
    session: str | None = MANAGER,
    identity: Mapping[str, str] | None = None,
) -> View:
    """Run the real `just unpublished` against the scratch registry and state home.

    ``identity`` is what a harness exports, handed through untouched, for the journeys
    over how the helper derives a session from it; ``session`` is the already-derived one.
    """
    environment = {
        name: value
        for name, value in seed.registry.environment.items()
        if name not in IDENTITY_NAMES
    }
    environment.update(identity or {})
    environment["XDG_STATE_HOME"] = str(seed.state if state is None else state)
    if session is not None:
        # What the harness exports and `scripts/launcher-session.sh` passes through: an
        # already-exported identity wins there, which is the arm a dispatch relies on.
        environment["ONEPIPELINE_LAUNCHER"] = "claude-code"
        environment["ONEPIPELINE_LAUNCHER_SESSION"] = session
    done = subprocess.run(
        ["just", "unpublished", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )
    return View(done.returncode, done.stdout, done.stderr)


def test_the_host_target_lists_every_preserved_branch_and_exits_counted(seed: Seeded) -> None:
    """`--host` is the answer from outside every checkout, which is the gap this closes.

    All three seeded states are here: the closed session's branch counted, the held
    session's marked in flight and not counted, and the orphan — a branch no session
    record names — listed, because nothing joins it to a session but it is still
    preserved work pinning a checkout.
    """
    view = _unpublished(seed, "--host", "--json", "--no-disk")
    assert view.status == COUNTED, f"expected a counted status\n{view.stdout}\n{view.stderr}"
    rows = view.rows()
    assert {seed.closed.branch, seed.held.branch, ORPHAN_BRANCH} <= set(rows)

    closed = rows[seed.closed.branch]
    assert closed["counted"] is True and closed["in_flight"] is False
    assert closed["session"] == seed.closed.token
    assert closed["identity"] == seed.registry.identity
    assert closed["base"] == "main"
    assert closed["landed"]["state"] in ("no", "unknown", "in-part")
    assert closed["resume_command"].startswith("just "), (
        "the resume command is rendered in its `just` form, because the raw "
        f"`onevcs publish-branch` line lands with an empty description: {closed}"
    )

    held = rows[seed.held.branch]
    assert held["in_flight"] is True and held["counted"] is False, (
        "a branch with an occupied run root is shown and marked, never counted: a publication "
        "landing it this moment is what a hook reading this view must not refuse a turn over"
    )
    recoverable = json.loads(
        seed.registry.onevcs("recoverable", "--repo", seed.registry.identity, "--json")
    )
    held_source = next(row for row in recoverable if row["branch"]["branch"] == seed.held.branch)
    assert held_source["held_by"]["holding"] == "run-root-occupied"

    orphan = rows[ORPHAN_BRANCH]
    assert orphan["session"] is None, "no session record names the orphan branch"


def test_a_real_run_root_lease_changes_what_onevcs_reports(
    tmp_path: Path,
) -> None:
    """The lease fixture must produce an actual holding, then release it."""
    registry = seeded(tmp_path)
    session = registry.open_session()

    def read() -> View:
        done = registry.run(
            "just",
            "unpublished",
            "--session",
            session.token,
            "--json",
            "--no-disk",
            XDG_STATE_HOME=str(tmp_path / "state"),
            ONEPIPELINE_LAUNCHER_SESSION=MANAGER,
            ONEPIPELINE_LAUNCHER="claude-code",
        )
        return View(done.returncode, done.stdout, done.stderr)

    # llmlint: ignore-block[tests_mirror_real_usage] A CLI session open cannot leave
    # its run-root lease held after exit. `hold_lease` takes the real onevcs 0.30.1
    # lock; this recipe journey proves the row changes when it is released.
    lease = registry.hold_lease(session)
    # llmlint: ignore-end[tests_mirror_real_usage]
    try:
        held = read()
        assert held.status == NOTHING_COUNTED, held.stderr
        assert held.rows()[session.branch]["in_flight"] is True
    finally:
        assert lease.stdin is not None
        lease.stdin.close()
        assert lease.wait(timeout=e2e_timeout(30)) == 0

    released = read()
    assert released.status == COUNTED, released.stderr
    assert released.rows()[session.branch]["in_flight"] is False

    # llmlint: ignore-block[tests_mirror_real_usage] OwnerRunning cannot survive
    # `onevcs session open` exiting; registry.hold writes onevcs's own liveness
    # fields for only that state. This recipe journey drives the real CLI beside it.
    owner = registry.hold(session)
    # llmlint: ignore-end[tests_mirror_real_usage]
    try:
        owner_held = read()
        assert owner_held.status == NOTHING_COUNTED, owner_held.stderr
        assert owner_held.rows()[session.branch]["in_flight"] is True
        source = json.loads(registry.onevcs("recoverable", "--repo", registry.identity, "--json"))
        record = next(
            (row for row in source if row["branch"]["branch"] == session.branch),
            None,
        )
        assert record is not None, source
        assert record["held_by"]["holding"] == "owner-running"
    finally:
        owner.kill()
        owner.wait(timeout=e2e_timeout(30))


def test_an_empty_host_inventory_exits_nothing_counted_through_the_recipe(
    seed: Seeded, tmp_path: Path
) -> None:
    home = tmp_path / "empty-onevcs"
    home.mkdir()
    environment = {
        name: value
        for name, value in seed.registry.environment.items()
        if name not in IDENTITY_NAMES
    }
    environment["ONEVCS_HOME"] = str(home)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    for arguments, expected in ((["--json"], "[]"), ([], "no preserved unpublished branch")):
        done = subprocess.run(
            ["just", "unpublished", "--host", *arguments],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(900),
            check=False,
        )
        assert done.returncode == NOTHING_COUNTED, done.stderr
        assert expected in done.stdout


def test_the_host_target_answers_for_every_identity_from_inside_a_registered_checkout(
    tmp_path: Path,
) -> None:
    """`--host` is the host's answer from any directory, a registered checkout included.

    The recipe runs from this checkout. Registering it in the scratch registry makes an
    unscoped `onevcs recoverable` answer for this identity alone, while the host view
    must still carry a branch of each scratch identity.
    """
    here = seeded(tmp_path / "here", name="here-checkout")
    elsewhere = seeded(tmp_path / "elsewhere", name="elsewhere-checkout")
    registered = here.run("just", "register-repo", str(elsewhere.checkout))
    assert registered.returncode == 0, registered.stderr + registered.stdout
    registered_root = here.run("just", "register-repo", str(REPO_ROOT))
    assert registered_root.returncode == 0, registered_root.stderr + registered_root.stdout
    # Named apart from this identity's orphan, so the row is told apart by branch.
    elsewhere_branch = "claude/elsewhere-work"
    git("branch", "-m", ORPHAN_BRANCH, elsewhere_branch, cwd=elsewhere.checkout)
    environment = {
        name: value for name, value in here.environment.items() if name not in IDENTITY_NAMES
    }
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    done = subprocess.run(
        ["just", "unpublished", "--host", "--json", "--no-disk"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )
    view = View(done.returncode, done.stdout, done.stderr)
    assert view.status == COUNTED, f"{view.stdout}\n{view.stderr}"
    rows = view.rows()
    assert rows[ORPHAN_BRANCH]["identity"] == here.identity
    assert elsewhere_branch in rows, (
        f"run inside {REPO_ROOT}, `--host` must still answer for {elsewhere.identity}: "
        f"{sorted(rows)}"
    )
    assert rows[elsewhere_branch]["identity"] == elsewhere.identity


def test_every_row_carries_exactly_the_shared_row_shape(seed: Seeded) -> None:
    """The row shape is a contract node `unfinished-guard` reads, so `--json` states it.

    The three label-fed fields are `null` here and named all the same: they are filled
    from the session labels the adopted `onevcs` carries, and a consumer that finds them
    missing rather than null would have to tell the two cases apart.
    """
    rows = _unpublished(seed, "--host", "--json", "--no-disk").rows()
    assert rows
    for branch, row in rows.items():
        assert tuple(row) == ROW_FIELDS, f"{branch} does not carry the row shape: {tuple(row)}"
        assert row["run"] is None and row["node"] is None and row["manager_session"] is None


def test_a_named_session_answers_for_its_own_branch_and_leaves_the_orphan_out(
    seed: Seeded,
) -> None:
    """`--session <s-token>` is the second target, and the orphan is host-level only.

    The branch is read off the session's holder record rather than derived from the
    token, because a retried session holds a branch named for an earlier one.
    """
    view = _unpublished(seed, "--session", seed.closed.token, "--json", "--no-disk")
    assert view.status == COUNTED
    rows = view.rows()
    assert set(rows) == {seed.closed.branch}
    assert ORPHAN_BRANCH not in rows, "a branch no session record names has no session target"
    assert seed.held.branch not in rows


def test_a_session_target_of_the_held_session_counts_nothing(seed: Seeded) -> None:
    """The status is about what is *counted*, not about whether rows were found."""
    view = _unpublished(seed, "--session", seed.held.token, "--json", "--no-disk")
    assert view.status == NOTHING_COUNTED, (
        f"the only row is in flight, so nothing is counted\n{view.stdout}\n{view.stderr}"
    )
    assert set(view.rows()) == {seed.held.branch}


def test_an_unknown_session_token_is_refused_by_the_filter_and_never_nothing(seed: Seeded) -> None:
    """A token no session record names is the adopted filter's refusal, said by name.

    Never `0`: nothing counted over a token nothing records would read as a session that
    left nothing behind.
    """
    view = _unpublished(seed, "--session", "s-00000deadbef", "--json", "--no-disk")
    assert view.status == UNANSWERED, view
    assert view.stdout == ""
    assert "s-00000deadbef" in view.stderr


def test_the_own_sessions_target_answers_the_launcher_labelled_branches(tmp_path: Path) -> None:
    """The default, `--own` and `--session <manager id>` are the one filtered read.

    Sessions opened with the labels the engine stamps — one for this manager's run, one
    for another manager's — and one opened with none: the manager's own target answers
    its own branch alone, its `run`, `node` and `manager_session` read off the labels, and
    the unlabelled branch is reached by `--host` only.
    """
    registry = seeded(tmp_path / "labelled")
    own = registry.open_session(labels={"run": "run-a", "node": "node-1", "launcher": MANAGER})
    registry.close_session(own)
    other = registry.open_session(
        labels={"run": "run-b", "node": "node-1", "launcher": OTHER_MANAGER}
    )
    registry.close_session(other)
    unlabelled = registry.open_session()
    registry.close_session(unlabelled)
    seed = Scope(registry, tmp_path / "state")

    for arguments, session in (
        ((), MANAGER),
        (("--own",), MANAGER),
        (("--session", MANAGER), None),
    ):
        view = _unpublished(seed, *arguments, "--json", "--no-disk", session=session)
        assert view.status == COUNTED, f"{arguments}: {view}"
        rows = view.rows()
        assert set(rows) == {own.branch}, f"{arguments} answered {sorted(rows)}"
        row = rows[own.branch]
        assert (row["run"], row["node"], row["manager_session"]) == ("run-a", "node-1", MANAGER)

    host = _unpublished(seed, "--host", "--json", "--no-disk").rows()
    assert unlabelled.branch in host and host[unlabelled.branch]["manager_session"] is None

    human = _unpublished(seed)
    assert human.status == COUNTED
    assert f"just unpublished --acknowledge {own.branch}" in human.stdout, (
        "a counted row names acknowledging with a reason as its way out"
    )
    assert "land it:" in human.stdout

    nobody = _unpublished(seed, "--own", session="a-worker-owning-nothing")
    assert nobody.status == NOTHING_COUNTED, nobody

    unidentified = _unpublished(seed, session=None)
    assert unidentified.status == REFUSED
    assert "no manager session identifies this shell" in unidentified.stderr
    assert unidentified.stdout.strip() == "", "a refusal answers with nothing on standard output"


def test_naming_both_targets_is_refused(seed: Seeded) -> None:
    view = _unpublished(seed, "--host", "--session", "s-000000000001")
    assert view.status == REFUSED
    assert "two targets" in view.stderr


def test_the_disk_reading_calls_out_the_planted_build_output(seed: Seeded) -> None:
    """The number a person acts on is the disk, so the run root and its build output.

    The planted `node_modules` is called out on its own and the sibling `scratch` is not:
    the reading is the declared tuple rather than everything under the worktree. The run
    root is the parent of the worktree — clone and worktree together — so it is larger.
    """
    rows = _unpublished(seed, "--host", "--json").rows()
    disk = rows[seed.held.branch]["disk"]
    assert disk["run_root"] == str(seed.held.run_root)
    assert set(disk["build_output"]) == {"node_modules"}, (
        f"only the declared build-output directories are called out: {disk}"
    )
    assert 300_000 <= disk["build_output"]["node_modules"] < 400_000
    assert disk["run_root_bytes"] > disk["build_output"]["node_modules"]


def test_no_disk_skips_the_walk_and_the_trailer_says_so(seed: Seeded) -> None:
    view = _unpublished(seed, "--host", "--json", "--no-disk")
    assert all(row["disk"] is None for row in view.rows().values())
    human = _unpublished(seed, "--host", "--no-disk")
    assert human.stdout.splitlines()[-2].endswith("disk not measured (--no-disk)")


def test_the_listing_names_both_ways_out_of_a_counted_row(seed: Seeded) -> None:
    """The default rendering is a table, the ways out, and a trailer.

    Both ways out are named beside each counted row, because the point of the view is
    that a manager can act on it: the `just` command that lands the branch, and the
    acknowledgement that records deliberately leaving it.
    """
    view = _unpublished(seed, "--host", "--no-disk")
    assert view.status == COUNTED
    lines = view.stdout.splitlines()
    assert lines[0].split() == [
        "IDENTITY",
        "BRANCH",
        "BASE",
        "LANDED",
        "SESSION",
        "RUN",
        "ROOT",
        "BUILD",
        "OUTPUT",
        "STANDING",
    ]
    assert any(line.split()[:1] == [seed.registry.identity] for line in lines)
    assert any("land it:" in line and "just " in line for line in lines)
    assert any(
        f"or acknowledge:  just unpublished --acknowledge {seed.closed.branch}" in line
        for line in lines
    )
    assert lines[-1] == (
        "Acknowledging is never landing: the row stays until its branch lands or is removed."
    )
    assert "counted of" in lines[-2] and "in flight" in lines[-2]


def test_the_trailer_states_the_counted_total_and_the_build_output_bytes(seed: Seeded) -> None:
    view = _unpublished(seed, "--host")
    trailer = view.stdout.splitlines()[-2]
    assert "run roots hold" in trailer and "of which build output is" in trailer


def test_an_acknowledgement_is_written_marks_its_row_and_is_the_session_s_alone(
    seed: Seeded, tmp_path: Path
) -> None:
    """Acknowledging is never landing: the row stays, uncounted, carrying its reason.

    And it is one session's record: a second manager session reading the same registry
    finds the branch counted, because the file is keyed on the session that wrote it.
    """
    recorded = _unpublished(
        seed,
        "--acknowledge",
        seed.closed.branch,
        "--reason",
        "the user is deciding whether to land it",
        state=tmp_path,
    )
    # Two branches counted before it — the closed session's and the orphan — and one
    # acknowledged: the status is the target's, so work is still owed.
    assert recorded.status == COUNTED, recorded.stderr
    assert seed.closed.branch in recorded.stdout

    # llmlint: ignore-block[tests_mirror_real_usage] The file is itself a stated contract,
    # not an internal: the node's criteria fix its location and its shape, and the `Stop`
    # hook a later node writes reads it from disk rather than through this recipe. So the
    # file is the one interface that contract reaches, and the rest of this test reads the
    # acknowledgement back through `just unpublished`.
    written = json.loads(
        next(
            (tmp_path / "ai-orchestrator" / "stop-unfinished" / "acknowledged").iterdir()
        ).read_text(encoding="utf-8")
    )
    assert written["version"] == 1
    [entry] = written["acknowledged"]
    assert entry["branch"] == seed.closed.branch
    assert entry["identity"] == seed.registry.identity
    assert entry["reason"] == "the user is deciding whether to land it"
    assert entry["at"].endswith("Z")
    assert len(entry["tip"]) == 40, "the acknowledgement is keyed on what the branch stands at"
    # llmlint: ignore-end[tests_mirror_real_usage]

    after = _unpublished(seed, "--host", "--json", "--no-disk", state=tmp_path)
    row = after.rows()[seed.closed.branch]
    assert row["counted"] is False, "an acknowledged branch is not counted"
    assert row["acknowledgement"]["reason"] == "the user is deciding whether to land it"
    assert after.rows()[ORPHAN_BRANCH]["counted"] is True
    assert after.status == COUNTED, "the orphan is still owed after the other is acknowledged"
    listing = _unpublished(seed, "--host", "--no-disk", state=tmp_path)
    assert "acknowledged: the user is deciding whether to land it" in listing.stdout
    assert listing.status == COUNTED

    other = _unpublished(
        seed, "--host", "--json", "--no-disk", state=tmp_path, session=OTHER_MANAGER
    )
    assert other.rows()[seed.closed.branch]["acknowledgement"] is None, (
        "an acknowledgement is invisible to every other manager session"
    )
    assert other.status == COUNTED


def test_an_acknowledgement_without_a_reason_is_refused_and_writes_nothing(
    seed: Seeded, tmp_path: Path
) -> None:
    """One carrying only a branch is indistinguishable from one nobody meant."""
    for arguments, said in (
        (("--acknowledge", seed.closed.branch), "needs a reason"),
        (("--acknowledge", seed.closed.branch, "--reason", "   "), "carries no visible character"),
        (("--acknowledge", seed.closed.branch, "--reason", ""), "carries no visible character"),
        (("--acknowledge", seed.closed.branch, "--reason", "a\tb"), "unprintable character"),
    ):
        view = _unpublished(seed, *arguments, state=tmp_path)
        assert view.status == REFUSED, f"{arguments} was not refused: {view}"
        assert said in view.stderr, view.stderr
    assert not (tmp_path / "ai-orchestrator").exists(), "a refused acknowledgement writes nothing"


def test_an_acknowledgement_that_cannot_be_written_is_said_and_never_a_status_of_nothing(
    seed: Seeded, tmp_path: Path
) -> None:
    """A state home the record cannot be written under answers neither `0` nor `7`.

    A regular file where the record's directory belongs is what a mistaken
    `XDG_STATE_HOME` looks like; the recipe says where it tried and what to do.
    """
    (tmp_path / "ai-orchestrator").write_text("not a directory\n", encoding="utf-8")
    view = _unpublished(
        seed, "--acknowledge", seed.closed.branch, "--reason", "why", state=tmp_path
    )
    assert view.status not in (NOTHING_COUNTED, COUNTED, REFUSED), view
    assert "the acknowledgement could not be written to" in view.stderr
    assert "XDG_STATE_HOME" in view.stderr
    assert view.stdout == ""


def test_acknowledging_a_branch_no_row_names_is_refused_naming_it(
    seed: Seeded, tmp_path: Path
) -> None:
    view = _unpublished(
        seed, "--acknowledge", "claude/never-existed", "--reason", "why", state=tmp_path
    )
    assert view.status == REFUSED
    assert "claude/never-existed" in view.stderr
    assert not (tmp_path / "ai-orchestrator").exists()


def test_a_branch_that_moves_past_its_acknowledgement_counts_again(
    seed: Seeded, tmp_path: Path
) -> None:
    """The acknowledgement is keyed on the tip, so new work on the branch re-raises it.

    This is the property the whole keying exists for: a session that acknowledged a
    branch and then committed more to it has left something new nobody has seen.
    """
    first = _unpublished(
        seed, "--acknowledge", seed.closed.branch, "--reason", "asked the user", state=tmp_path
    )
    assert first.status == COUNTED, f"the orphan is still owed: {first.stderr}"
    acknowledged = _unpublished(
        seed, "--acknowledge", ORPHAN_BRANCH, "--reason", "left on purpose", state=tmp_path
    )
    assert acknowledged.status == NOTHING_COUNTED, (
        f"every counted branch of the target is now acknowledged: {acknowledged.stderr}"
    )
    before = _unpublished(seed, "--host", "--json", "--no-disk", state=tmp_path)
    assert before.rows()[ORPHAN_BRANCH]["counted"] is False
    assert before.status == NOTHING_COUNTED

    commit_on(seed.registry.checkout, ORPHAN_BRANCH, "more-work.txt")

    after = _unpublished(seed, "--host", "--json", "--no-disk", state=tmp_path)
    row = after.rows()[ORPHAN_BRANCH]
    assert row["counted"] is True, "a branch past its acknowledged tip counts again"
    assert row["acknowledgement"] is None
    assert after.status == COUNTED


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param({}, id="nothing-exported"),
        pytest.param({"CLAUDE_CODE_SESSION_ID": "not a session id"}, id="unusable-id"),
    ],
)
def test_an_unidentified_session_cannot_acknowledge(
    seed: Seeded, tmp_path: Path, identity: dict[str, str]
) -> None:
    """A session nothing identifies stays unidentified, so there is nothing to key on.

    An id `scripts/launcher-session.sh` refuses the shape of is the same answer: the
    helper leaves the process unattributed rather than keying on a string no later read
    can match.
    """
    view = _unpublished(
        seed,
        "--acknowledge",
        seed.closed.branch,
        "--reason",
        "why",
        state=tmp_path,
        session=None,
        identity=identity,
    )
    assert view.status == REFUSED
    assert "session" in view.stderr
    assert not (tmp_path / "ai-orchestrator").exists()


def test_a_session_id_of_another_shape_keys_no_acknowledgement_by_either_road(
    seed: Seeded, tmp_path: Path
) -> None:
    """The session an acknowledgement is keyed on is validated by the module itself.

    Through the recipe, `scripts/launcher-session.sh` keeps an inherited id of another
    shape — its run was launched under it — so the module is what refuses to key on it;
    and a caller running the module directly, as the later `Stop` hook will, gets the
    same refusal. Neither writes anything.
    """
    malformed = "not a session id"
    through_recipe = _unpublished(
        seed,
        "--acknowledge",
        seed.closed.branch,
        "--reason",
        "why",
        state=tmp_path,
        session=None,
        identity={"ONEPIPELINE_LAUNCHER_SESSION": malformed},
    )
    assert through_recipe.status == REFUSED, through_recipe
    assert "not the shape a session id has" in through_recipe.stderr

    environment = {
        name: value
        for name, value in seed.registry.environment.items()
        if name not in IDENTITY_NAMES
    }
    environment.update(
        {
            "XDG_STATE_HOME": str(tmp_path),
            "PYTHONPATH": str(REPO_ROOT),
            "ONEPIPELINE_LAUNCHER_SESSION": malformed,
        }
    )
    directly = subprocess.run(
        [
            sys.executable,
            "-m",
            "orchestrator.unpublished",
            "--acknowledge",
            seed.closed.branch,
            "--reason",
            "why",
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert directly.returncode == REFUSED, directly.stderr
    assert "not the shape a session id has" in directly.stderr
    assert not (tmp_path / "ai-orchestrator").exists(), "a refused acknowledgement writes nothing"


def test_print_surface_prints_the_vocabulary_through_the_wrapper() -> None:
    """`--print-surface` is how the guard node's drift test reads the contract."""
    done = subprocess.run(
        [str(REPO_ROOT / "scripts" / "unpublished.sh"), "--print-surface"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    for status, name in (
        (NOTHING_COUNTED, "nothing-counted"),
        (COUNTED, "counted"),
        (REFUSED, "refused"),
    ):
        assert f"status {status} {name}" in lines
    assert [line for line in lines if line.startswith("field ")] == [
        f"field {field}" for field in ROW_FIELDS
    ]
    for label in ("run", "node", "launcher"):
        assert f"label {label}" in lines
    # The stated values, spelled out rather than read back from the module, so a line a
    # consumer branches on cannot disappear from the wrapper's output unnoticed.
    assert [line for line in lines if line.startswith("build-output ")] == [
        f"build-output {name}" for name in ("target", "node_modules", ".venv", ".nx", "dist")
    ]
    assert [line for line in lines if line.startswith("counts ")] == [
        f"counts {state}" for state in ("no", "unknown", "in-part")
    ]


def test_the_names_cleared_are_every_name_the_identity_helper_reads() -> None:
    """`IDENTITY_NAMES` restates the helper's inputs, so it is read back against them.

    A name the helper started reading that this list did not clear would let the harness
    running this suite key an acknowledgement instead of the session a test names.
    """
    # llmlint: ignore-block[tests_mirror_real_usage] A drift gate, not a behavioural
    # test: `IDENTITY_NAMES` restates the helper's inputs, and the only way to hold a
    # restated list to its source is to read that source. The behaviour those names drive
    # is proven through the recipe by the journey below.
    read = {
        name
        for pair in HELPER_NAMES.findall(LAUNCHER_SESSION_SH.read_text(encoding="utf-8"))
        for name in pair
        if name
    }
    assert read == set(IDENTITY_NAMES)
    # llmlint: ignore-end[tests_mirror_real_usage]


@pytest.mark.parametrize(
    ("exported", "keyed_on", "passed_over"),
    [
        pytest.param({"CLAUDE_CODE_SESSION_ID": "claude-1"}, "claude-1", None, id="claude-code"),
        pytest.param({"CLAUDE_SESSION_ID": "claude-2"}, "claude-2", None, id="claude-fallback"),
        pytest.param({"CODEX_THREAD_ID": "codex-1"}, "codex-1", None, id="codex"),
        pytest.param({"CODEX_SESSION_ID": "codex-2"}, "codex-2", None, id="codex-fallback"),
        pytest.param(
            {"CODEX_THREAD_ID": "codex-1", "CLAUDE_CODE_SESSION_ID": "claude-1"},
            "claude-1",
            "codex-1",
            id="claude-before-codex",
        ),
        pytest.param(
            {"ONEPIPELINE_LAUNCHER_SESSION": "planner-1", "CLAUDE_CODE_SESSION_ID": "claude-1"},
            "planner-1",
            "claude-1",
            id="an-exported-identity-wins",
        ),
    ],
)
def test_an_acknowledgement_is_keyed_on_the_session_the_harness_exports(
    seed: Seeded,
    tmp_path: Path,
    exported: dict[str, str],
    keyed_on: str,
    passed_over: str | None,
) -> None:
    """The recipe derives the manager session itself, from what the harness exports.

    Read back through the recipe: the session the helper should have derived sees the
    acknowledgement, and the one it should have passed over does not.
    """
    view = _unpublished(
        seed,
        "--acknowledge",
        seed.closed.branch,
        "--reason",
        "keyed on the harness",
        state=tmp_path,
        session=None,
        identity=exported,
    )
    assert view.status == COUNTED, f"the orphan is still owed: {view.stderr}"
    seen = _unpublished(seed, "--host", "--json", "--no-disk", state=tmp_path, session=keyed_on)
    acknowledgement = seen.rows()[seed.closed.branch]["acknowledgement"]
    assert acknowledgement is not None, f"the acknowledgement must be keyed on {keyed_on!r}"
    assert acknowledgement["reason"] == "keyed on the harness"
    if passed_over is not None:
        other = _unpublished(
            seed, "--host", "--json", "--no-disk", state=tmp_path, session=passed_over
        )
        assert other.rows()[seed.closed.branch]["acknowledgement"] is None, (
            f"{passed_over!r} is the session the helper should have passed over"
        )


def _scratch_checkout(root: Path, helper: str | None) -> Path:
    """A checkout holding the real wrapper and module, and ``helper`` as its identity helper.

    ``None`` leaves the helper out. No `.venv`, so the wrapper reaches for the system
    interpreter as a fresh clone's does.
    """
    (root / "scripts").mkdir(parents=True)
    (root / "orchestrator").mkdir()
    shutil.copy2(UNPUBLISHED_SH, root / "scripts" / "unpublished.sh")
    shutil.copy2(MODULE, root / "orchestrator" / "unpublished.py")
    shutil.copy2(REPO_ROOT / "orchestrator" / "root.py", root / "orchestrator" / "root.py")
    if helper is not None:
        (root / "scripts" / "launcher-session.sh").write_text(helper, encoding="utf-8")
    return root / "scripts" / "unpublished.sh"


def _wrapper(script: Path, path: str) -> subprocess.CompletedProcess[str]:
    """Run outside either checkout, with ``path`` as the whole search path."""
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(script), "--print-surface"],
        cwd=script.parent.parent.parent,
        env={"PATH": path, "HOME": str(script.parent)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )


def _search_path(directory: Path, *tools: str) -> str:
    """A directory holding exactly ``tools``, linked to where this host keeps each."""
    directory.mkdir()
    for tool in tools:
        found = shutil.which(tool) if tool != "python3" else sys.executable
        assert found is not None, tool
        (directory / tool).symlink_to(found)
    return str(directory)


@pytest.mark.parametrize(
    ("helper", "refusal"),
    [
        pytest.param(None, "required helper is not a readable regular file: ", id="missing"),
        pytest.param("false\n", "is readable but could not be loaded", id="fails-to-load"),
    ],
)
def test_the_wrapper_refuses_an_identity_helper_it_cannot_use(
    tmp_path: Path, helper: str | None, refusal: str
) -> None:
    """The wrapper names `scripts/launcher-session.sh` itself when it cannot source it."""
    script = _scratch_checkout(tmp_path / "checkout", helper)
    done = _wrapper(script, _search_path(tmp_path / "bin", "dirname", "python3"))
    assert done.returncode == UNANSWERED, done.stderr
    assert refusal in done.stderr
    assert str(script.parent / "launcher-session.sh") in done.stderr, (
        "the refusal must name the helper it could not use"
    )
    assert done.stdout == ""


def test_the_wrapper_falls_back_to_the_system_interpreter_and_refuses_without_one(
    tmp_path: Path,
) -> None:
    """A checkout with no `.venv` runs the module under `python3` from the search path."""
    script = _scratch_checkout(
        tmp_path / "checkout", LAUNCHER_SESSION_SH.read_text(encoding="utf-8")
    )
    found = _wrapper(script, _search_path(tmp_path / "with", "dirname", "python3"))
    assert found.returncode == 0, found.stderr
    assert f"status {COUNTED} counted" in found.stdout.splitlines()

    missing = _wrapper(script, _search_path(tmp_path / "without", "dirname"))
    assert missing.returncode == UNANSWERED
    assert "found no python3" in missing.stderr


def test_the_wrapper_says_what_failed_when_its_interpreter_will_not_execute(
    tmp_path: Path,
) -> None:
    """An interpreter that passes the executable check and then fails to start.

    A `.venv` whose base interpreter was removed is that case: the wrapper names the
    interpreter and what to do about it, rather than leaving bash's own line as the
    whole report.
    """
    script = _scratch_checkout(
        tmp_path / "checkout", LAUNCHER_SESSION_SH.read_text(encoding="utf-8")
    )
    interpreter = script.parent.parent / ".venv" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(f"#!{tmp_path / 'removed' / 'python3'}\n", encoding="utf-8")
    interpreter.chmod(0o755)
    done = _wrapper(script, _search_path(tmp_path / "bin", "dirname", "python3"))
    assert done.returncode == UNANSWERED, done.stderr
    assert f"could not execute the interpreter {interpreter}" in done.stderr
    assert "just bootstrap" in done.stderr
    assert done.stdout == ""


def test_the_wrapper_refuses_a_checkout_missing_the_module(tmp_path: Path) -> None:
    """A checkout without the module is refused by the wrapper, naming the file to restore."""
    script = _scratch_checkout(
        tmp_path / "checkout", LAUNCHER_SESSION_SH.read_text(encoding="utf-8")
    )
    module = script.parent.parent / "orchestrator" / "unpublished.py"
    module.unlink()
    done = _wrapper(script, _search_path(tmp_path / "bin", "dirname", "python3"))
    assert done.returncode == UNANSWERED, done.stderr
    assert f"the view's module is not a readable regular file: {module}" in done.stderr
    assert done.stdout == ""
