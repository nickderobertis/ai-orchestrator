"""`orchestrator/unpublished.py`: the view, its contract, and every arm of its reader.

The recipe is driven end to end by `tests/e2e/unpublished_view/test_unpublished_e2e.py`; this
tier runs the same module **in process**, against the same real `onevcs` over a scratch registry
`tests/unpublished_registry.py` seeds, because the coverage floor over `orchestrator/`
is measured in process and a subprocess proves nothing to it. Nothing about the view is
doubled: `onevcs`, git, the registry and the sessions are real, and the failure arms are
reached by breaking the real thing — a registry that will not parse, a binary that will
not execute, a directory that will not read — never by substituting the reader.

Two things here are contract reconciliations rather than journeys: the resume-command
table is held to the one `scripts/recoverable.sh` rewrites with, and the row shape
`--json` emits is held to :data:`ROW_FIELDS`, which node `unfinished-guard` reads.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
module drives the real `onevcs` and stays in `orchestrator:test` deliberately, and the
reason is the coverage floor rather than convenience: `orchestrator/project.json` runs
`test-recipes` with `--no-cov` and gives `coverage` a `dependsOn` of `test` alone, so
`orchestrator:test` is the one tier whose measurement the 100% floor over `orchestrator/`
is read from. Moving this module to a host-tool tier would take
`orchestrator/unpublished.py` out of that measurement entirely — the floor would then be
met by a file nothing exercised. The recipe half of these journeys *is* split out, into
`tests/e2e/unpublished_view/test_unpublished_e2e.py` in a project of its own; what is left here
is the in-process half, which is the half a subprocess cannot prove to `coverage`.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import pytest
from unpublished_registry import ORPHAN_BRANCH, Registry, Session, commit_on, git, seeded

from orchestrator import unpublished
from orchestrator.root import REPO_ROOT
from orchestrator.unpublished import (
    COUNTED,
    NOTHING_COUNTED,
    REFUSED,
    ROW_FIELDS,
    UNANSWERED,
    Acknowledgement,
    Branch,
    Holder,
    Identity,
    Row,
    SessionToken,
    Unanswered,
)

#: The recipe script whose resume-command table this module's :data:`RECIPES` mirrors.
RECOVERABLE_SH = REPO_ROOT / "scripts" / "recoverable.sh"
RECIPES_LINE = re.compile(r"^RECIPES = (\{.*\})$", re.MULTILINE)

#: The helper that establishes the manager session, whose exported name the module reads.
LAUNCHER_SESSION_SH = REPO_ROOT / "scripts" / "launcher-session.sh"
EXPORTED = re.compile(r"^\s*export (ONEPIPELINE_[A-Z_]+)=", re.MULTILINE)
#: The shape the helper checks a session id against, as bash spells the pattern.
USABLE_SESSION = re.compile(r'\[\[ "\$1" =~ (\S+) \]\]')

#: The manager session every acknowledgement here is keyed on, and a second one that
#: must never see it.
MANAGER = "manager-session-1"
OTHER_MANAGER = "manager-session-2"


class Seeded(NamedTuple):
    """The registry with one branch in each state, and the live owner to kill."""

    registry: Registry
    closed: Session
    held: Session
    owner: subprocess.Popen[bytes]


@pytest.fixture(scope="module")
def seeded_registry(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Seeded]:
    """One registry, one seeding, for every read-only journey in this module.

    The mutating journeys — an acknowledgement, a new commit — each work on a branch of
    their own or under a state home of their own, so the seed is shared safely.
    """
    registry = seeded(tmp_path_factory.mktemp("unpublished"))
    closed = registry.open_session()
    registry.close_session(closed)
    held = registry.open_session()
    owner = registry.hold(held)
    # Build output under the live session's worktree — one directory the view calls out,
    # one it does not, and a symbolic link out to a tree whose bytes must not be counted.
    (held.worktree / "target").mkdir()
    (held.worktree / "target" / "blob").write_bytes(b"\0" * 300_000)
    (held.worktree / "target" / "elsewhere").symlink_to(REPO_ROOT)
    (held.worktree / "scratch").mkdir()
    (held.worktree / "scratch" / "notes").write_bytes(b"\0" * 1_000)
    (held.worktree / "elsewhere").symlink_to(REPO_ROOT)
    try:
        yield Seeded(registry, closed, held, owner)
    finally:
        owner.kill()
        owner.wait()


def _run(
    registry: Registry,
    *arguments: str,
    monkeypatch: pytest.MonkeyPatch,
    state: Path,
    session: str | None = MANAGER,
) -> tuple[int, str, str]:
    """Run the module's `main` in process against ``registry``."""
    monkeypatch.setenv("ONEVCS_HOME", str(registry.home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    if session is None:
        monkeypatch.delenv(unpublished.LAUNCHER_SESSION_ENV, raising=False)
    else:
        monkeypatch.setenv(unpublished.LAUNCHER_SESSION_ENV, session)
    out, err = io.StringIO(), io.StringIO()
    status = unpublished.main(list(arguments), out=out, err=err)
    return status, out.getvalue(), err.getvalue()


def _rows(stdout: str) -> dict[str, dict[str, object]]:
    rows = json.loads(stdout)
    assert isinstance(rows, list)
    return {row["branch"]: row for row in rows}


def test_the_resume_command_table_is_the_one_recoverable_sh_rewrites_with() -> None:
    """One mapping from `onevcs`'s landing verbs to the `just` recipes, in two files.

    `scripts/recoverable.sh` rewrites the verb's human listing and this module rewrites
    its JSON, and both exist because the raw `onevcs publish-branch` line lands with an
    empty description. The shell copy is a Python literal inside a quoted program, so it
    is read back as one here.
    """
    found = RECIPES_LINE.search(RECOVERABLE_SH.read_text(encoding="utf-8"))
    assert found, f"{RECOVERABLE_SH} no longer states a RECIPES table to reconcile against"
    assert json.loads(found.group(1).replace("'", '"')) == unpublished.RECIPES


def test_the_session_an_acknowledgement_is_keyed_on_is_the_name_the_helper_exports() -> None:
    """One name in two files: the shell that exports it, and the module that reads it.

    `scripts/launcher-session.sh` is the one definition of who is acting on this host, and
    `scripts/unpublished.sh` sources it so this module can read the session an
    acknowledgement is keyed on. A rename on either side would not fail anywhere else: the
    module would simply find nothing, and every acknowledgement would be refused for want
    of a session nobody could see was still being exported.
    """
    exported = set(EXPORTED.findall(LAUNCHER_SESSION_SH.read_text(encoding="utf-8")))
    assert exported, f"{LAUNCHER_SESSION_SH} no longer exports a name to reconcile against"
    assert unpublished.LAUNCHER_SESSION_ENV in exported, (
        f"{unpublished.LAUNCHER_SESSION_ENV} is what this module reads the manager session "
        f"from, and {LAUNCHER_SESSION_SH.name} exports {sorted(exported)}"
    )


def test_the_session_shape_is_the_one_the_helper_checks() -> None:
    """The module re-checks the session id the helper already judged, by the same shape.

    Two statements of one boundary: the helper's `launcher_session_is_usable` and
    :data:`SESSION_ID`. A shape either side widened alone would key an acknowledgement on
    an id the other refuses, so the two are read against each other here.
    """
    found = USABLE_SESSION.search(LAUNCHER_SESSION_SH.read_text(encoding="utf-8"))
    assert found is not None, f"{LAUNCHER_SESSION_SH} no longer checks a session's shape"
    assert found.group(1) == unpublished.SESSION_ID.pattern


def test_resume_command_renders_each_verb_as_its_recipe_and_nothing_else() -> None:
    assert (
        unpublished.resume_command(["onevcs", "publish-branch", "b", "--repo", "/a b"])
        == "just publish-branch b --repo '/a b'"
    )
    assert unpublished.resume_command(["onevcs", "recover", "b"]) == "just repo-recover b"
    assert unpublished.resume_command(["onevcs", "integrate", "b"]) == "just integrate b"
    assert unpublished.resume_command([]) is None, "a landed row's empty argv renders nothing"
    assert unpublished.resume_command(["onevcs", "recoverable"]) is None
    assert unpublished.resume_command(["git", "push"]) is None
    assert unpublished.resume_command(["onevcs", "publish-branch", 7]) is None, (  # type: ignore[list-item]
        "`shlex.join` raises on a non-string, which would end the listing rather than a row"
    )


def test_print_surface_states_every_shared_constant() -> None:
    out = io.StringIO()
    unpublished.print_surface(out)
    lines = out.getvalue().splitlines()
    assert "status 0 nothing-counted" in lines
    assert "status 7 counted" in lines
    assert "status 2 refused" in lines
    assert "status 1 unanswered" in lines
    assert [line for line in lines if line.startswith("label ")] == [
        f"label {label}" for label in unpublished.SESSION_LABELS
    ]
    assert [line for line in lines if line.startswith("field ")] == [
        f"field {field}" for field in ROW_FIELDS
    ]
    assert "in-flight owner-running" in lines
    assert "in-flight run-root-occupied" in lines
    assert [line for line in lines if line.startswith("build-output ")] == [
        f"build-output {name}" for name in unpublished.BUILD_OUTPUT_DIRECTORIES
    ]
    assert [line for line in lines if line.startswith("counts ")] == [
        f"counts {state}" for state in unpublished.COUNTED_LANDED_STATES
    ]
    assert "acknowledgement-version 1" in lines
    assert "acknowledgements-under ai-orchestrator/stop-unfinished/acknowledged" in lines


def test_print_surface_defaults_to_standard_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert unpublished.main(["--print-surface"]) == NOTHING_COUNTED
    assert "status 7 counted" in capsys.readouterr().out


def test_acknowledgement_file_honours_only_an_absolute_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    digest = "5d0e3d5d47d8a1bf0aa6fb4a1e7e28c1bb00d1f3fb2c4f0b4f79b1e0a2f7d2c9"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    absolute = unpublished.acknowledgement_file("s")
    assert absolute.parent == tmp_path / "ai-orchestrator" / "stop-unfinished" / "acknowledged"
    assert absolute.name.endswith(".json") and len(absolute.stem) == len(digest)
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    assert unpublished.acknowledgement_file("s").parent == (
        Path.home() / ".local" / "state" / "ai-orchestrator" / "stop-unfinished" / "acknowledged"
    )
    monkeypatch.delenv("XDG_STATE_HOME")
    assert unpublished.acknowledgement_file("s") == unpublished.acknowledgement_file("s")
    assert unpublished.acknowledgement_file("s") != unpublished.acknowledgement_file("t")


def test_host_reading_lists_every_state_and_counts_only_the_preserved_ones(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One row per preserved branch, each carrying exactly the contract's fields.

    The closed session and the orphan count; the held one is in flight and does not,
    while still being shown. The disk reading follows no symbolic link: the live
    worktree's `target` is measured as the bytes written there, not as this repository.
    """
    seed = seeded_registry
    status, out, err = _run(
        seed.registry, "--host", "--json", monkeypatch=monkeypatch, state=tmp_path
    )

    assert status == COUNTED, err
    rows = _rows(out)
    assert set(rows) == {ORPHAN_BRANCH, seed.closed.branch, seed.held.branch}, err
    for row in rows.values():
        assert tuple(row) == ROW_FIELDS, f"a row's fields are not the contract's: {tuple(row)}"
        assert row["identity"] == seed.registry.identity
        assert row["base"] == "main"
        assert row["provenance"] == "complete"
        assert row["landed"] == {"state": "no"}
        assert row["change_url"] is None
        assert row["run"] is None and row["node"] is None and row["manager_session"] is None
        assert row["acknowledgement"] is None

    orphan = rows[ORPHAN_BRANCH]
    assert orphan["session"] is None
    assert orphan["counted"] is True and orphan["in_flight"] is False
    assert "no session record" in str(orphan["stopped_because"])
    assert orphan["resume_command"] == (
        f"just publish-branch {ORPHAN_BRANCH} --repo {seed.registry.checkout}"
    )
    assert orphan["disk"] == {"run_root": None, "run_root_bytes": None, "build_output": {}}

    closed = rows[seed.closed.branch]
    assert closed["session"] == seed.closed.token
    assert closed["counted"] is True and closed["in_flight"] is False
    disk = closed["disk"]
    assert isinstance(disk, dict)
    assert disk["run_root"] == str(seed.closed.run_root)
    assert isinstance(disk["run_root_bytes"], int) and disk["run_root_bytes"] > 0
    assert disk["build_output"] == {}, "a closed session's worktree is gone with the session"

    held = rows[seed.held.branch]
    assert held["session"] == seed.held.token
    assert held["in_flight"] is True and held["counted"] is False
    assert "still running" in str(held["stopped_because"])
    disk = held["disk"]
    assert isinstance(disk, dict)
    assert disk["run_root"] == str(seed.held.run_root)
    assert set(disk["build_output"]) == {"target"}, "only the declared directories are called out"
    assert 300_000 <= disk["build_output"]["target"] < 400_000, (
        "the symbolic link under `target` was followed, or the blob was not counted"
    )
    assert disk["run_root_bytes"] < 5_000_000, "a symbolic link out of the worktree was followed"
    assert disk["run_root_bytes"] > disk["build_output"]["target"]


def test_the_human_rendering_is_a_table_with_the_ways_out_and_a_trailer(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = seeded_registry
    status, out, err = _run(seed.registry, "--host", monkeypatch=monkeypatch, state=tmp_path)

    assert status == COUNTED, err
    lines = out.splitlines()
    assert [cell.strip() for cell in re.split(r"  +", lines[0]) if cell.strip()] == [
        "IDENTITY",
        "BRANCH",
        "BASE",
        "LANDED",
        "SESSION",
        "RUN ROOT",
        "BUILD OUTPUT",
        "STANDING",
    ]
    table = {line.split()[1]: line for line in lines[1:4]}
    assert table[seed.held.branch].rstrip().endswith("in flight")
    assert "target=" in table[seed.held.branch]
    assert table[seed.closed.branch].rstrip().endswith("counted")
    assert table[ORPHAN_BRANCH].rstrip().endswith("counted")
    for branch in (seed.closed.branch, ORPHAN_BRANCH):
        assert (
            f"    land it:         just publish-branch {branch} --repo {seed.registry.checkout}"
            in lines
        )
        assert (
            f"    or acknowledge:  just unpublished --acknowledge {branch} "
            '--reason "<why it is deliberately left>"'
        ) in lines
    assert not any(seed.held.branch in line and "land it" in line for line in lines), (
        "an in-flight branch was offered a landing command"
    )
    trailer = lines[-2]
    assert trailer.startswith(
        "2 counted of 3 preserved unpublished branch(es); 1 in flight, 0 acknowledged; "
    )
    assert "run roots hold" in trailer and "build output is" in trailer
    assert lines[-1].startswith("Acknowledging is never landing")


def test_no_disk_skips_the_walk_and_says_so(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = seeded_registry
    status, out, _ = _run(
        seed.registry, "--host", "--json", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
    )
    assert status == COUNTED
    assert all(row["disk"] is None for row in _rows(out).values())
    status, out, _ = _run(
        seed.registry, "--host", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
    )
    assert status == COUNTED
    assert out.splitlines()[-2].endswith("disk not measured (--no-disk)")


def test_the_session_target_answers_for_the_named_tokens_and_never_the_orphan(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = seeded_registry
    status, out, err = _run(
        seed.registry,
        "--session",
        seed.closed.token,
        "--json",
        "--no-disk",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == COUNTED, err
    assert set(_rows(out)) == {seed.closed.branch}

    status, out, err = _run(
        seed.registry,
        "--session",
        seed.held.token,
        "--json",
        "--no-disk",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == NOTHING_COUNTED, "an in-flight row is shown and never counted"
    rows = _rows(out)
    assert set(rows) == {seed.held.branch} and rows[seed.held.branch]["in_flight"] is True

    status, out, err = _run(
        seed.registry,
        "--session",
        seed.closed.token,
        "--session",
        seed.held.token,
        "--session",
        "s-0000deadbeef",
        "--json",
        "--no-disk",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == COUNTED
    assert set(_rows(out)) == {seed.closed.branch, seed.held.branch}
    assert "session s-0000deadbeef: no registered identity records a session by that token" in err


def test_a_retried_session_holding_an_earlier_tokens_branch_is_joined_off_its_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two records name one branch; the open one is the session, and both tokens find it."""
    registry = seeded(tmp_path / "seed")
    first = registry.open_session()
    registry.close_session(first)
    retry = registry.open_session(branch=first.branch)
    assert retry.branch == first.branch and retry.token != first.token

    for token in (first.token, retry.token):
        status, out, err = _run(
            registry,
            "--session",
            token,
            "--json",
            "--no-disk",
            monkeypatch=monkeypatch,
            state=tmp_path,
        )
        assert status == COUNTED, err
        rows = _rows(out)
        assert set(rows) == {first.branch}
        assert rows[first.branch]["session"] == retry.token, "the open record names the session"


@pytest.mark.parametrize(
    "arguments",
    [(), ("--own",), ("--session", "0b2c-manager-id"), ("--session", "s-000000000001\n")],
)
def test_the_own_sessions_target_is_refused_naming_the_labels_it_awaits(
    seeded_registry: Seeded,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    status, out, err = _run(
        seeded_registry.registry, *arguments, monkeypatch=monkeypatch, state=tmp_path
    )
    assert status == REFUSED
    assert out == ""
    assert "refused" in err and "run, node, launcher" in err and "unfinished-guard" in err


def test_two_targets_at_once_are_refused(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    status, _, err = _run(
        seeded_registry.registry,
        "--host",
        "--session",
        "s-000000000abc",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == REFUSED and "two targets" in err


def test_acknowledging_marks_the_row_for_this_session_alone_until_the_branch_moves(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = seeded_registry
    branch = seed.closed.branch
    status, out, err = _run(
        seed.registry,
        "--acknowledge",
        branch,
        "--reason",
        "  waiting on the user  ",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == COUNTED, f"the orphan still counts, so the target is still owed: {err}"
    assert out.startswith(f"acknowledged {branch} [{seed.registry.identity}] at ")
    written = unpublished.acknowledgement_file(MANAGER)
    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["version"] == 1
    [entry] = document["acknowledged"]
    assert set(entry) == {"branch", "identity", "tip", "reason", "at"}
    assert entry["branch"] == branch and entry["identity"] == seed.registry.identity
    assert entry["reason"] == "waiting on the user", "the reason is kept trimmed"
    assert re.fullmatch(r"[0-9a-f]{40}", entry["tip"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", entry["at"])

    status, out, err = _run(
        seed.registry, "--host", "--json", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
    )
    assert status == COUNTED, "the orphan still counts"
    row = _rows(out)[branch]
    assert row["counted"] is False and row["acknowledgement"] == entry
    status, out, _ = _run(
        seed.registry, "--host", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
    )
    assert f"acknowledged: {entry['reason']}" in out
    assert "1 counted of 3 preserved unpublished branch(es); 1 in flight, 1 acknowledged" in out

    for other in (OTHER_MANAGER, None):
        status, out, _ = _run(
            seed.registry,
            "--host",
            "--json",
            "--no-disk",
            monkeypatch=monkeypatch,
            state=tmp_path,
            session=other,
        )
        assert (
            _rows(out)[branch]["counted"] is True and _rows(out)[branch]["acknowledgement"] is None
        )

    status, _, err = _run(
        seed.registry,
        "--acknowledge",
        branch,
        "--reason",
        "still waiting",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == COUNTED, err
    [replaced] = json.loads(written.read_text(encoding="utf-8"))["acknowledged"]
    assert replaced["reason"] == "still waiting" and replaced["tip"] == entry["tip"]
    status, _, err = _run(
        seed.registry,
        "--acknowledge",
        ORPHAN_BRANCH,
        "--reason",
        "left for the user",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == NOTHING_COUNTED, f"the last counted branch is acknowledged: {err}"

    commit_on(seed.registry.checkout, branch, "more.txt")
    status, out, _ = _run(
        seed.registry, "--host", "--json", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
    )
    row = _rows(out)[branch]
    assert row["counted"] is True and row["acknowledgement"] is None


def test_an_acknowledgement_without_a_reason_or_of_no_row_writes_nothing(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = seeded_registry
    for arguments, said in (
        (("--acknowledge", ORPHAN_BRANCH), "needs a reason"),
        (("--acknowledge", ORPHAN_BRANCH, "--reason", "   "), "carries no visible character"),
        (("--acknowledge", ORPHAN_BRANCH, "--reason", ""), "carries no visible character"),
        (("--acknowledge", ORPHAN_BRANCH, "--reason", "\u200b"), "carries no visible character"),
        (
            ("--acknowledge", ORPHAN_BRANCH, "--reason", "one\ntwo"),
            "unprintable character(s) '\\n'",
        ),
        (("--acknowledge", "claude/never-was", "--reason", "why"), "claude/never-was is on no row"),
    ):
        status, out, err = _run(seed.registry, *arguments, monkeypatch=monkeypatch, state=tmp_path)
        assert status == REFUSED, err
        assert out == "" and said in err
    assert not unpublished.acknowledgement_file(MANAGER).exists()
    status, _, err = _run(
        seed.registry,
        "--acknowledge",
        ORPHAN_BRANCH,
        "--reason",
        "why",
        monkeypatch=monkeypatch,
        state=tmp_path,
        session=None,
    )
    assert status == REFUSED and "no manager session identifies this shell" in err


def test_an_acknowledgement_that_cannot_be_written_is_unanswered_and_keeps_the_record(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A write that fails says where and what to do, and leaves the earlier record whole."""
    seed = seeded_registry
    acknowledge = ("--acknowledge", ORPHAN_BRANCH, "--reason", "first")
    status, _, err = _run(seed.registry, *acknowledge, monkeypatch=monkeypatch, state=tmp_path)
    assert status == COUNTED, f"the closed branch still counts: {err}"
    path = unpublished.acknowledgement_file(MANAGER)
    before = path.read_text(encoding="utf-8")
    path.parent.chmod(0o500)
    try:
        status, out, err = _run(
            seed.registry,
            "--acknowledge",
            ORPHAN_BRANCH,
            "--reason",
            "second",
            monkeypatch=monkeypatch,
            state=tmp_path,
        )
    finally:
        path.parent.chmod(0o700)
    assert status == UNANSWERED, err
    assert out == "" and f"could not be written to {path}" in err and "XDG_STATE_HOME" in err
    assert path.read_text(encoding="utf-8") == before, "the earlier record stands whole"
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name], "nothing left behind"


def test_a_session_id_of_another_shape_is_refused_for_an_acknowledgement_and_keys_no_reading(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The session is validated here, not only by the wrapper's helper.

    The helper keeps an inherited id of another shape, and a caller may run this module
    directly, so an acknowledgement under one is refused naming the fault and writes
    nothing; a listing still answers, reading no acknowledgement, so every branch counts.
    """
    seed = seeded_registry
    for malformed in ("has space", "../../etc", "-leading-hyphen", "x" * 201, "valid\n"):
        status, out, err = _run(
            seed.registry,
            "--acknowledge",
            ORPHAN_BRANCH,
            "--reason",
            "why",
            monkeypatch=monkeypatch,
            state=tmp_path,
            session=malformed,
        )
        assert status == REFUSED, (malformed, err)
        assert out == "" and "not the shape a session id has" in err
    assert not (tmp_path / "ai-orchestrator").exists(), "a refused acknowledgement writes nothing"
    status, out, err = _run(
        seed.registry,
        "--host",
        "--json",
        "--no-disk",
        monkeypatch=monkeypatch,
        state=tmp_path,
        session="has space",
    )
    assert status == COUNTED
    assert "no acknowledgement is read, so every branch counts" in err
    assert _rows(out)[ORPHAN_BRANCH]["acknowledgement"] is None


def test_the_host_target_answers_for_every_identity_from_inside_a_registered_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--host` is the host's answer wherever it runs, a registered checkout included.

    Unscoped, `onevcs recoverable` run inside a registered checkout answers for that
    checkout's identity alone; so the reading here is taken with the working directory
    inside one identity's checkout, and must still carry the other identity's branch.
    """
    registry = seeded(tmp_path / "one")
    other = seeded(tmp_path / "two", name="other-checkout")
    registered = registry.run("just", "register-repo", str(other.checkout))
    assert registered.returncode == 0, registered.stderr
    # Named apart from the first identity's orphan, so the row is told apart by branch.
    other_branch = "claude/elsewhere"
    git("branch", "-m", ORPHAN_BRANCH, other_branch, cwd=other.checkout)
    monkeypatch.chdir(registry.checkout)
    status, out, err = _run(
        registry, "--host", "--json", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
    )
    assert status == COUNTED, err
    rows = _rows(out)
    assert rows[ORPHAN_BRANCH]["identity"] == registry.identity
    assert rows[other_branch]["identity"] == other.identity, (
        f"run inside {registry.checkout}, `--host` must still answer for {other.identity}"
    )


def test_a_branch_preserved_on_two_identities_cannot_be_acknowledged_by_name_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = seeded(tmp_path / "one")
    other = seeded(tmp_path / "two", name="other-checkout")
    other_registered = registry.run("just", "register-repo", str(other.checkout))
    assert other_registered.returncode == 0, other_registered.stderr
    status, _, err = _run(
        registry,
        "--acknowledge",
        ORPHAN_BRANCH,
        "--reason",
        "why",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == REFUSED and "preserved on several identities" in err
    status, out, err = _run(
        registry, "--host", "--json", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
    )
    assert status == COUNTED
    assert sorted(row["identity"] for row in json.loads(out)) == sorted(
        [registry.identity, other.identity]
    )


def test_an_acknowledgement_file_that_cannot_be_read_is_reported_and_stands_for_nothing(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = seeded_registry
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = unpublished.acknowledgement_file(MANAGER)
    path.parent.mkdir(parents=True)
    tip = git("rev-parse", ORPHAN_BRANCH, cwd=seed.registry.checkout).strip()
    duplicate = {
        "branch": ORPHAN_BRANCH,
        "identity": seed.registry.identity,
        "tip": tip,
        "reason": "left deliberately",
        "at": "2026-01-01T00:00:00Z",
    }
    for content, said in (
        ("not json", "could not be read"),
        (json.dumps({"version": 2, "acknowledged": []}), "not a version 1"),
        (json.dumps([]), "not a version 1"),
        (
            json.dumps({"version": 1, "acknowledged": [{"branch": ORPHAN_BRANCH}]}),
            "not an acknowledgement",
        ),
        (
            json.dumps(
                {
                    "version": 1,
                    "acknowledged": [
                        {
                            "branch": ORPHAN_BRANCH,
                            "identity": seed.registry.identity,
                            "tip": "0" * 40,
                            "reason": "left deliberately",
                            "at": "not a timestamp",
                        }
                    ],
                }
            ),
            "not an acknowledgement",
        ),
        (
            json.dumps(
                {
                    "version": 1,
                    "acknowledged": [
                        {
                            "branch": ORPHAN_BRANCH,
                            "identity": seed.registry.identity,
                            "tip": "0" * 40,
                            "reason": "left deliberately",
                            "at": "2026-1-1T00:00:00Z",
                        }
                    ],
                }
            ),
            "not an acknowledgement",
        ),
        (json.dumps({"version": 1, "acknowledged": "x"}), "acknowledged is not an array"),
        (
            json.dumps({"version": 1, "acknowledged": [duplicate, duplicate]}),
            "duplicate acknowledgement",
        ),
    ):
        path.write_text(content, encoding="utf-8")
        status, out, err = _run(
            seed.registry, "--host", "--json", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
        )
        assert status == COUNTED
        assert said in err, err
        assert _rows(out)[ORPHAN_BRANCH]["counted"] is True
    path.unlink()
    path.mkdir()
    status, _, err = _run(
        seed.registry, "--host", "--json", "--no-disk", monkeypatch=monkeypatch, state=tmp_path
    )
    assert status == COUNTED and "could not be read" in err


def test_a_damaged_existing_acknowledgement_is_kept_when_a_new_one_is_requested(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = unpublished.acknowledgement_file(MANAGER)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    status, out, err = _run(
        seeded_registry.registry,
        "--acknowledge",
        seeded_registry.closed.branch,
        "--reason",
        "new reason",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == UNANSWERED and out == ""
    assert "existing acknowledgement file could not be read completely" in err
    assert path.read_text(encoding="utf-8") == "{broken"


def test_a_tip_that_cannot_be_read_leaves_no_acknowledgement_standing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conservative reading: a branch whose tip is unreadable counts."""
    row = Row(
        identity=Identity("id"),
        branch=Branch("b"),
        base="main",
        provenance="complete",
        landed={"state": "no"},
        change_url=None,
        stopped_because="",
        checkout=tmp_path / "not-a-checkout",
        resume=None,
        held_by=None,
        holder=None,
    )
    warnings: list[str] = []
    standing = unpublished._standing(
        row, [Acknowledgement(Branch("b"), Identity("id"), "tip", "why", "at")], warnings
    )
    assert standing is None
    assert warnings and "does not stand" in warnings[0]
    assert unpublished.counted(row, None) is True
    (tmp_path / "no-git").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "no-git"))
    with pytest.raises(Unanswered, match="could not run"):
        unpublished._tip(row)

    # A `git` that exits 0 with something other than an object name is the one answer a
    # real checkout never gives, so it is stood up at the executable boundary here.
    speaking = tmp_path / "odd-git"
    speaking.mkdir()
    (speaking / "git").write_text("#!/bin/sh\necho not-a-tip\n", encoding="utf-8")
    (speaking / "git").chmod(0o755)
    monkeypatch.setenv("PATH", str(speaking))
    with pytest.raises(Unanswered, match="'not-a-tip' on standard output"):
        unpublished._tip(row)


def test_a_registry_onevcs_refuses_is_unanswered_never_a_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "broken-home"
    home.mkdir()
    (home / "registry.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--host"], out=out, err=err) == UNANSWERED
    assert out.getvalue() == ""
    assert "could not answer" in err.getvalue() and "exited" in err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--session", "s-000000000abc"], out=out, err=err) == UNANSWERED


def test_an_empty_registry_answers_nothing_for_a_session_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    out, err = io.StringIO(), io.StringIO()
    assert (
        unpublished.main(["--session", "s-000000000abc", "--json"], out=out, err=err)
        == NOTHING_COUNTED
    )
    assert json.loads(out.getvalue()) == []
    assert "no registered identity records a session by that token" in err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--session", "s-000000000abc"], out=out, err=err) == NOTHING_COUNTED
    assert out.getvalue().strip() == "no preserved unpublished branch for this target"


def test_a_repos_listing_this_view_cannot_read_is_unanswered_not_nothing_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `onevcs repos` line the view cannot place must never read as an empty host.

    `--session` looks each token up in the identities this listing names, so a listing
    read short answers *no registered identity records that token* over branches that
    are really held — a false `NOTHING_COUNTED` a consumer would act on. The real
    `onevcs` is asked here and its real empty listing is what answers; what moves is the
    one line the view accepts as that answer, so the line the tool actually prints
    arrives as the drift a later release would bring.
    """
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setattr(unpublished, "NO_IDENTITIES", "no identities are registered")
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    out, err = io.StringIO(), io.StringIO()
    assert (
        unpublished.main(["--session", "s-000000000abc", "--json"], out=out, err=err) == UNANSWERED
    )
    assert out.getvalue() == ""
    assert "cannot read as an identity" in err.getvalue()
    assert "no repositories registered" in err.getvalue()


def test_a_malformed_checkout_row_cannot_shorten_the_host_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An indented row in an unknown format may hide an identity from this reader."""
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    original = unpublished._onevcs

    def changed_listing(*args: str) -> str:
        answer = original(*args)
        return answer + "  checkout-without-a-path\n" if args == ("repos",) else answer

    monkeypatch.setattr(unpublished, "_onevcs", changed_listing)
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--host", "--json"], out=out, err=err) == UNANSWERED
    assert out.getvalue() == ""
    assert "checkout line this view cannot read" in err.getvalue()


def test_a_malformed_identity_row_cannot_shorten_the_host_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A future repos format must not silently create the wrong identity."""
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    original = unpublished._onevcs

    def changed_listing(*args: str) -> str:
        answer = original(*args)
        return answer + "github.com/owner/repo\tgate\textra\n" if args == ("repos",) else answer

    monkeypatch.setattr(unpublished, "_onevcs", changed_listing)
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--host", "--json"], out=out, err=err) == UNANSWERED
    assert out.getvalue() == ""
    assert "identity line this view cannot read" in err.getvalue()


def test_an_empty_repos_answer_is_not_a_declared_empty_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    original = unpublished._onevcs

    def empty_listing(*args: str) -> str:
        answer = original(*args)
        return " \n" if args == ("repos",) else answer

    monkeypatch.setattr(unpublished, "_onevcs", empty_listing)
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--host", "--json"], out=out, err=err) == UNANSWERED
    assert out.getvalue() == ""
    assert "no rows and no declared empty response" in err.getvalue()


def test_a_blank_line_beside_a_declared_repos_answer_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("ONEVCS_HOME", str(home))
    original = unpublished._onevcs

    def listing_with_blank_line(*args: str) -> str:
        answer = original(*args)
        return " \n" + answer if args == ("repos",) else answer

    monkeypatch.setattr(unpublished, "_onevcs", listing_with_blank_line)
    assert unpublished._identities() == []


def test_a_missing_or_unrunnable_onevcs_is_unanswered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The binary is this checkout's `.venv/bin` first and the search path after."""
    monkeypatch.setattr(unpublished, "REPO_ROOT", tmp_path / "no-such-checkout")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    (tmp_path / "empty-bin").mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--host"], out=out, err=err) == UNANSWERED
    assert "found no `onevcs` to ask" in err.getvalue()

    unrunnable = tmp_path / "no-such-checkout" / ".venv" / "bin"
    unrunnable.mkdir(parents=True)
    (unrunnable / "onevcs").write_bytes(b"\0\1\2 not an executable format")
    (unrunnable / "onevcs").chmod(0o755)
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--host"], out=out, err=err) == UNANSWERED
    assert "could not run" in err.getvalue()


def test_the_search_path_answers_when_the_checkout_has_no_onevcs(
    seeded_registry: Seeded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = REPO_ROOT / ".venv" / "bin" / "onevcs"
    assert installed.is_file(), "this checkout is not provisioned"
    on_path = tmp_path / "bin"
    on_path.mkdir()
    (on_path / "onevcs").symlink_to(installed)
    monkeypatch.setattr(unpublished, "REPO_ROOT", tmp_path / "no-such-checkout")
    # `onevcs` spawns git itself, so git's own directory stays on the path beside it.
    git = shutil.which("git")
    assert git is not None
    monkeypatch.setenv("PATH", os.pathsep.join([str(on_path), str(Path(git).parent)]))
    status, out, err = _run(
        seeded_registry.registry,
        "--host",
        "--json",
        "--no-disk",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == COUNTED, err


def test_a_read_past_its_bound_is_unanswered(
    seeded_registry: Seeded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(unpublished, "ONEVCS_TIMEOUT_SECONDS", 0.001)
    status, out, err = _run(
        seeded_registry.registry, "--host", monkeypatch=monkeypatch, state=tmp_path
    )
    assert status == UNANSWERED and out == ""
    assert "ran past its 0.001s bound" in err


def test_an_answer_that_is_not_an_array_of_objects_is_unanswered() -> None:
    with pytest.raises(Unanswered, match="not JSON"):
        unpublished._json_array("nope", "x")
    with pytest.raises(Unanswered, match="not an array of objects"):
        unpublished._json_array('{"a": 1}', "x")
    with pytest.raises(Unanswered, match="not an array of objects"):
        unpublished._json_array("[1]", "x")
    assert unpublished._json_array("[]", "x") == []


def test_an_identity_whose_sessions_cannot_be_read_is_a_warning_not_a_status(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONEVCS_HOME", str(seeded_registry.registry.home))
    warnings: list[str] = []
    assert unpublished._holders(Identity("/nowhere/registered"), warnings) == []
    assert warnings and "its sessions could not be read" in warnings[0]


def test_a_session_target_whose_sessions_cannot_be_read_is_unanswered_never_nothing(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--session` cannot say a token is held nowhere when an identity went unread.

    The identity it cannot read may be the one holding the named session, so answering
    *nothing counted* would be a false answer a consumer acts on. The real `onevcs` is
    asked and refuses; what moves is the identity listing, to one it has no record of,
    because the pinned `onevcs` answers `session holders` for every identity its own
    `repos` lists — a missing checkout, origin or sessions directory included — so no
    registry this suite can build reaches this through `repos` alone.
    """
    monkeypatch.setattr(unpublished, "_identities", lambda: [Identity("/nowhere/registered")])
    status, out, err = _run(
        seeded_registry.registry,
        "--session",
        seeded_registry.closed.token,
        "--json",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == UNANSWERED, err
    assert out == "" and "could not answer" in err and "session holders" in err


def test_a_row_or_holder_record_missing_its_names_is_left_out_and_said() -> None:
    warnings: list[str] = []
    assert unpublished._row({"identity": "x"}, [], warnings) is None
    assert (
        unpublished._row({"identity": "x", "branch": {"branch": 1}, "checkout": "/c"}, [], warnings)
        is None
    )
    assert (
        unpublished._row(
            {"identity": "x", "branch": {"branch": "b"}, "checkout": "relative/c"}, [], warnings
        )
        is None
    ), "a relative checkout is left out: it is what the acknowledgement's tip is read in"
    assert len(warnings) == 3 and all("was left out" in warning for warning in warnings)
    row = unpublished._row(
        {"identity": "x", "branch": {"branch": "b"}, "checkout": "/c", "landed": "odd"},
        [],
        warnings,
    )
    assert row is not None
    assert row.landed == {"state": "unknown"}, "an unreadable landing is the *may have landed* row"
    assert row.base == "" and row.resume is None and row.held_by is None and row.holder is None
    assert row.run_root is None and row.session is None
    assert unpublished.counted(row, None) is True, "`unknown` counts"

    held = row._replace(
        held_by={
            "holding": "run-root-occupied",
            "token": "s-000000000001",
            "worktree": "/runs/s-000000000001/worktree",
        }
    )
    assert (
        held.in_flight
        and held.run_root == Path("/runs/s-000000000001")
        and held.session == "s-000000000001"
    )
    assert unpublished.counted(held, None) is False
    for holding in (*unpublished.IN_FLIGHT_HOLDINGS, "a-variant-onevcs-adds-later"):
        carried = row._replace(held_by={"holding": holding})
        assert carried.in_flight, (
            "a `held_by` is in flight whatever its holding value: every variant of that "
            "enum is a session that has not finished with the branch, and one onevcs adds "
            "must not be counted for not being on a list here"
        )
        assert unpublished.counted(carried, None) is False
    assert not row._replace(held_by=None).in_flight
    assert unpublished.counted(row._replace(landed={"state": "in-part"}), None) is True
    assert unpublished.counted(row._replace(landed={"state": "yes"}), None) is False


def test_a_field_onevcs_states_as_something_other_than_text_is_defaulted_not_stringified() -> None:
    """Every rendered field is taken when `onevcs` gave a string and defaulted otherwise.

    Coercing with `str()` here would write a nested object's Python repr into a row a
    person reads and into the JSON another consumer parses, which is worse than the
    default because it looks like a value `onevcs` stated. The shapes below are ones the
    installed `onevcs` never writes, so the reader is handed them directly.
    """
    warnings: list[str] = []
    row = unpublished._row(
        {
            "identity": "i",
            "checkout": "/c",
            "landed": {"state": "no"},
            "branch": {
                "branch": "b",
                "base": {"unexpected": "object"},
                "provenance": 7,
                "change_url": {"unexpected": "object"},
            },
            "stopped_because": ["unexpected", "array"],
        },
        [],
        warnings,
    )
    assert row is not None
    assert row.base == "" and row.provenance == ""
    assert row.stopped_because == ""
    assert row.change_url is None, "a change URL that is not a string is no change URL"

    stated = unpublished._row(
        {
            "identity": "i",
            "checkout": "/c",
            "landed": {"state": "no"},
            "branch": {
                "branch": "b",
                "base": "main",
                "provenance": "complete",
                "change_url": "https://example.invalid/1",
            },
            "stopped_because": "the run was stopped",
        },
        [],
        warnings,
    )
    assert stated is not None
    assert stated.base == "main" and stated.provenance == "complete"
    assert stated.stopped_because == "the run was stopped"
    assert stated.change_url == "https://example.invalid/1"

    [holder] = unpublished._parse_holders(
        [
            {
                "token": "s-000000000001",
                "branch": "b",
                "worktree": "/r/w",
                "identity": {"unexpected": "object"},
                "state": 3,
            }
        ],
        Identity("fallback-identity"),
        warnings,
    )
    [unnamed] = unpublished._parse_holders(
        [{"token": "s-000000000001", "branch": "b", "worktree": "/r/w", "identity": ""}],
        Identity("fallback-identity"),
        warnings,
    )
    assert unnamed.identity == "fallback-identity", "an empty identity joins nothing"
    assert holder.identity == "fallback-identity", (
        "an identity `onevcs` did not state as text falls back to the one that was asked for"
    )
    assert holder.state is None
    assert len(warnings) == 2 and all("read as not open" in w for w in warnings), (
        "a state `onevcs` did not state as one of its lifecycle words is said, never stringified"
    )


def test_special_modes_exclude_every_other_declared_option() -> None:
    """A new parser flag cannot silently pass through a mode that ignores it."""
    options = {action.dest for action in unpublished._parser()._actions if action.dest != "help"}
    assert set(unpublished.ALONE["--print-surface"]) == options - {"print_surface"}
    assert set(unpublished.ALONE["--acknowledge"]) == options - {
        "print_surface",
        "acknowledge",
        "reason",
    }


def test_an_invocation_naming_two_modes_is_refused_rather_than_ranked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A combination that would silently drop half of what was asked is refused.

    Each of these reads to a caller as having done both things: a listing and an
    acknowledgement, a vocabulary and a listing, a reason and nothing to give it to.
    """
    for arguments, named in (
        (["--print-surface", "--host"], "--host"),
        (["--print-surface", "--acknowledge", "b", "--reason", "why"], "--acknowledge"),
        (["--acknowledge", "b", "--reason", "why", "--host"], "--host"),
        (["--acknowledge", "b", "--reason", "why", "--json"], "--json"),
        (["--acknowledge", "b", "--reason", "why", "--no-disk"], "--no-disk"),
    ):
        out, err = io.StringIO(), io.StringIO()
        assert unpublished.main(arguments, out=out, err=err) == REFUSED, arguments
        assert named in err.getvalue(), (arguments, err.getvalue())
        assert out.getvalue() == "", "a refused invocation answers with nothing"

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    for arguments in (["--host", "--reason", "why"], ["--host", "--reason", ""]):
        out, err = io.StringIO(), io.StringIO()
        assert unpublished.main(arguments, out=out, err=err) == REFUSED, arguments
        assert "`--reason`" in err.getvalue() and "nothing here is" in err.getvalue()
    out, err = io.StringIO(), io.StringIO()
    assert unpublished.main(["--print-surface", "--reason", ""], out=out, err=err) == REFUSED
    assert "`--reason`" in err.getvalue() and out.getvalue() == ""


def test_an_acknowledgement_record_must_carry_a_reason_and_a_tip_to_suppress_a_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Read-side and write-side hold the same rules, because the file is what counts.

    A blank reason is refused at `--acknowledge`; an entry hand-edited to carry one would
    otherwise suppress a count that same way, and a tip that is not an object name can
    never match one and only hides why the branch still counts.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    tip = "0" * 40
    good = {
        "branch": "b",
        "identity": "i",
        "tip": tip,
        "reason": "left on purpose",
        "at": "2026-01-01T00:00:00Z",
    }
    path = unpublished.acknowledgement_file("s")
    path.parent.mkdir(parents=True, exist_ok=True)
    for entry, kept in (
        (good, True),
        ({**good, "reason": "   "}, False),
        ({**good, "reason": "hand-edited\ninto two lines"}, False),
        ({**good, "tip": "not-an-object-name"}, False),
        ({**good, "tip": tip + "\n"}, False),
        ({**good, "tip": 7}, False),
        ("not even an object", False),
    ):
        path.write_text(
            json.dumps({"version": 1, "acknowledged": [entry]}) + "\n", encoding="utf-8"
        )
        warnings: list[str] = []
        read = unpublished._read_acknowledgements("s", warnings)
        assert bool(read) is kept, entry
        assert bool(warnings) is not kept


def test_holder_records_are_read_at_the_boundary(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real records read whole; a record naming no token, branch or worktree is left out
    and said — a shape the installed `onevcs` never writes, so the parser is handed one."""
    seed = seeded_registry
    monkeypatch.setenv("ONEVCS_HOME", str(seed.registry.home))
    warnings: list[str] = []
    holders = unpublished._holders(Identity(seed.registry.identity), warnings)
    assert {str(h.token) for h in holders} == {seed.closed.token, seed.held.token}, warnings
    assert {h.branch for h in holders} == {seed.closed.branch, seed.held.branch}
    assert {h.state for h in holders} == {"closed", "open"}
    assert warnings == []

    parsed = unpublished._parse_holders(
        [
            {"token": "s-000000000001", "branch": "b"},
            {"token": "s-000000000003", "branch": "b", "worktree": "relative/w"},
            {"token": "s-000000000002", "branch": "b", "worktree": "/r/w"},
            {"token": "s-000000000004", "branch": "b", "worktree": "/r/w", "state": "retired"},
        ],
        Identity("i"),
        warnings,
    )
    assert [h.token for h in parsed] == ["s-000000000002", "s-000000000004"], (
        "a relative worktree is left out: it would be walked against whatever directory "
        "this was run from"
    )
    assert parsed[0].identity == "i"
    assert [h.state for h in parsed] == [None, None], (
        "a state outside onevcs's lifecycle — or none — is read as not open"
    )
    assert len(warnings) == 4
    assert all("was left out" in warning for warning in warnings[:2])
    assert all("read as not open" in warning for warning in warnings[2:])
    assert "retired" in warnings[3]

    assert unpublished._holder_for(
        [
            Holder(
                SessionToken("s-00000000000a"), Identity("i"), Branch("b"), Path("/a/w"), "closed"
            ),
            Holder(
                SessionToken("s-00000000000b"), Identity("i"), Branch("b"), Path("/b/w"), "open"
            ),
        ],
        Identity("i"),
        Branch("b"),
    ) == Holder(SessionToken("s-00000000000b"), Identity("i"), Branch("b"), Path("/b/w"), "open")
    assert unpublished._holder_for(parsed, Identity("i"), Branch("b")) == parsed[0]
    assert unpublished._holder_for([], Identity("i"), Branch("b")) is None

    with pytest.raises(Unanswered, match="different identity"):
        unpublished._parse_holders(
            [{"token": "s-000000000001", "branch": "b", "worktree": "/r/w", "identity": "other"}],
            Identity("i"),
            [],
        )


def test_a_scoped_recoverable_row_cannot_name_another_identity(
    seeded_registry: Seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = Identity(seeded_registry.registry.identity)
    monkeypatch.setenv("ONEVCS_HOME", str(seeded_registry.registry.home))
    original = unpublished._onevcs

    def changed_identity(*args: str) -> str:
        answer = original(*args)
        if args[:1] == ("recoverable",):
            rows = json.loads(answer)
            rows[0]["identity"] = "another identity"
            return json.dumps(rows)
        return answer

    monkeypatch.setattr(unpublished, "_onevcs", changed_identity)
    with pytest.raises(Unanswered, match="answered a row for identity"):
        unpublished._recoverable(identity)


@pytest.mark.parametrize("malformed_source", ["holder", "recoverable"])
def test_a_malformed_producer_row_is_reported_by_a_complete_session_view(
    seeded_registry: Seeded,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    malformed_source: str,
) -> None:
    """Drive the view's public entry point with a malformed external CLI answer.

    The installed onevcs only emits valid records, so its output is changed at the
    subprocess boundary for this failure case. All target selection, joining, rendering,
    diagnostics, and exit classification still run through ``main``.
    """
    seed = seeded_registry
    original = unpublished._onevcs

    def malformed_answer(*arguments: str) -> str:
        answer = original(*arguments)
        if malformed_source == "holder" and arguments[:2] == ("session", "holders"):
            holders = json.loads(answer)
            for holder in holders:
                if holder["token"] == seed.closed.token:
                    holder["worktree"] = "relative/worktree"
            return json.dumps(holders)
        if malformed_source == "recoverable" and arguments[:1] == ("recoverable",):
            rows = json.loads(answer)
            for row in rows:
                if row["branch"]["branch"] == seed.closed.branch:
                    row["checkout"] = "relative/checkout"
            return json.dumps(rows)
        return answer

    monkeypatch.setattr(unpublished, "_onevcs", malformed_answer)
    status, out, err = _run(
        seed.registry,
        "--session",
        seed.closed.token,
        "--json",
        "--no-disk",
        monkeypatch=monkeypatch,
        state=tmp_path,
    )
    assert status == NOTHING_COUNTED
    assert json.loads(out) == []
    assert "was left out" in err
    assert "relative/" in err


def test_empty_names_and_a_token_of_another_shape_are_left_out_and_said() -> None:
    warnings: list[str] = []
    for record in (
        {"identity": "", "branch": {"branch": "b"}, "checkout": "/c"},
        {"identity": "x", "branch": {"branch": ""}, "checkout": "/c"},
    ):
        assert unpublished._row(record, [], warnings) is None, record
    parsed = unpublished._parse_holders(
        [
            {"token": "manager-session-1", "branch": "b", "worktree": "/r/w"},
            {"token": "s-000000000001\n", "branch": "b", "worktree": "/r/w"},
            {"token": "s-000000000001", "branch": "", "worktree": "/r/w"},
            {"token": "s-000000000002", "branch": "b", "worktree": "/r/w", "state": "open"},
        ],
        Identity("i"),
        warnings,
    )
    assert [h.token for h in parsed] == ["s-000000000002"], (
        "a row joins on the token and the branch"
    )
    assert len(warnings) == 5 and all("was left out" in warning for warning in warnings)


def test_a_landing_or_holder_onevcs_states_unreadably_is_read_conservatively_and_said() -> None:
    base = {"identity": "x", "branch": {"branch": "b"}, "checkout": "/c"}
    warnings: list[str] = []
    row = unpublished._row({**base, "landed": {"state": 1, "unlanded": 3}}, [], warnings)
    assert row is not None and row.landed == {"state": "unknown"}
    assert unpublished.counted(row, None) is True, "a landing nothing can read still counts"
    assert len(warnings) == 1 and "landing could not be read" in warnings[0]

    warnings.clear()
    row = unpublished._row({**base, "landed": {"state": "a-state-onevcs-adds"}}, [], warnings)
    assert row is not None and row.landed == {"state": "unknown"}
    assert unpublished.counted(row, None) is True, (
        "a state this view has no ruling on must never quietly uncount a preserved branch"
    )
    assert len(warnings) == 1 and "no ruling on" in warnings[0]
    for known in (*unpublished.COUNTED_LANDED_STATES, unpublished.LANDED_STATE):
        warnings.clear()
        row = unpublished._row({**base, "landed": {"state": known}}, [], warnings)
        assert row is not None and row.landed == {"state": known} and warnings == []

    warnings.clear()
    stated = {
        "holding": "owner-running",
        "token": "s-000000000009",
        "worktree": "/runs/s-000000000009/worktree",
    }
    row = unpublished._row(
        {**base, "landed": {"state": "no"}, "held_by": {**stated, "pid": 7}}, [], warnings
    )
    assert row is not None and row.held_by == stated and warnings == [], (
        "a field this view does not read is dropped silently: it changes nothing here"
    )

    for held_by in ("odd", {"holding": 1, "token": "manager-1", "worktree": "relative/w"}):
        warnings.clear()
        row = unpublished._row(
            {**base, "landed": {"state": "no"}, "held_by": held_by}, [], warnings
        )
        assert row is not None and row.held_by == {}, held_by
        assert row.in_flight and not unpublished.counted(row, None), (
            "onevcs reported a holder, so the row is in flight whatever it said about it"
        )
        assert row.session is None and row.run_root is None
        assert len(warnings) == 1 and "holder could not be read whole" in warnings[0]


def test_the_disk_walk_reports_what_it_cannot_read_and_a_run_root_that_is_gone(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "runs" / "s-000000000001"
    (run_root / "worktree" / "node_modules").mkdir(parents=True)
    (run_root / "worktree" / "node_modules" / "a").write_bytes(b"x" * 10)
    allocated = (run_root / "worktree" / "node_modules" / "a").stat().st_blocks * 512
    (run_root / "worktree" / "dist").symlink_to(tmp_path)
    os.mkfifo(run_root / "worktree" / "node_modules" / "pipe")  # neither a file nor a directory
    sealed = run_root / "worktree" / "sealed"
    sealed.mkdir()
    (sealed / "hidden").write_bytes(b"x" * 10)
    sealed.chmod(0)
    if os.access(sealed, os.R_OK):
        pytest.skip("this user can read a mode-0 directory, so nothing here is unreadable")
    holder = Holder(
        SessionToken("s-000000000001"), Identity("i"), Branch("b"), run_root / "worktree", "closed"
    )
    row = Row(
        identity=Identity("i"),
        branch=Branch("b"),
        base="main",
        provenance="complete",
        landed={"state": "no"},
        change_url=None,
        stopped_because="",
        checkout=tmp_path,
        resume=None,
        held_by=None,
        holder=holder,
    )
    warnings: list[str] = []
    try:
        disk = unpublished._disk(row, warnings)
    finally:
        sealed.chmod(stat.S_IRWXU)
    assert disk.run_root == run_root
    assert disk.run_root_bytes == allocated, (
        "the sealed directory's bytes were counted, or the link followed"
    )
    assert disk.build_output == {"node_modules": allocated}, (
        "a symbolic link named like build output is not one"
    )
    assert warnings and "could not be read while measuring disk" in warnings[0]
    assert disk.entry() == {
        "run_root": str(run_root),
        "run_root_bytes": allocated,
        "build_output": {"node_modules": allocated},
    }, "the row's spelling of the reading is the model's own, rendered once"

    gone = row._replace(
        holder=holder._replace(worktree=tmp_path / "runs" / "s-000000000002" / "worktree")
    )
    warnings = []
    disk = unpublished._disk(gone, warnings)
    assert disk == unpublished.Disk(
        run_root=tmp_path / "runs" / "s-000000000002", run_root_bytes=None, build_output={}
    )
    assert disk.entry()["run_root_bytes"] is None, (
        "a reading that could not be taken is null, which a reader tells from a measured zero"
    )
    assert warnings and "is not a directory" in warnings[0]

    linked_root = tmp_path / "runs" / "s-000000000003"
    linked_root.symlink_to(run_root, target_is_directory=True)
    linked = row._replace(holder=holder._replace(worktree=linked_root / "worktree"))
    warnings = []
    assert unpublished._disk(linked, warnings).run_root_bytes is None
    assert "traverses a symbolic link" in warnings[0]

    linked_worktree_root = tmp_path / "runs" / "s-000000000004"
    linked_worktree_root.mkdir()
    (linked_worktree_root / "worktree").symlink_to(run_root / "worktree", target_is_directory=True)
    linked_worktree = row._replace(
        holder=holder._replace(worktree=linked_worktree_root / "worktree")
    )
    warnings = []
    linked_disk = unpublished._disk(linked_worktree, warnings)
    assert linked_disk.run_root_bytes == 0 and linked_disk.build_output == {}
    assert "is a symbolic link" in warnings[0]

    rootless = row._replace(holder=None)
    assert unpublished._disk(rootless, []).entry() == {
        "run_root": None,
        "run_root_bytes": None,
        "build_output": {},
    }, "a row pinning no run root renders every reading as null"

    for elsewhere in (
        tmp_path / "pool" / "1" / "worktree",  # a pooled slot: the next session's disk
        tmp_path / "runs" / "manager-1" / "worktree",
        tmp_path / "runs" / "s-000000000001" / "checkout",
        Path("/"),
    ):
        warnings = []
        outside = row._replace(holder=holder._replace(worktree=elsewhere))
        assert outside.run_root is None, elsewhere
        assert unpublished._disk(outside, warnings).run_root_bytes is None
        assert len(warnings) == 1 and "is not under a run root" in warnings[0], warnings


def test_bytes_render_as_a_person_reads_them() -> None:
    assert unpublished._human_bytes(None) == "-"
    assert unpublished._human_bytes(0) == "0B"
    assert unpublished._human_bytes(1023) == "1023B"
    assert unpublished._human_bytes(1536) == "1.5K"
    assert unpublished._human_bytes(35 * 1024**3) == "35.0G"
    assert unpublished._human_bytes(3 * 1024**4) == "3.0T"
    assert unpublished._human_bytes(2048 * 1024**4) == "2048.0T"


def test_an_empty_listing_says_so() -> None:
    out = io.StringIO()
    unpublished._table([], out)
    assert out.getvalue().strip() == "no preserved unpublished branch for this target"


def test_the_table_names_a_landed_row_and_offers_no_command_it_does_not_have() -> None:
    """Two shapes the installed `onevcs` never lists without `--all`, rendered all the same.

    A row whose landing is decided is neither counted nor in flight nor acknowledged, so
    its standing names the landing; a counted row `onevcs` gave no command for is offered
    the acknowledgement alone rather than a `land it:` line naming nothing.
    """
    base = dict.fromkeys(ROW_FIELDS)
    landed = {
        **base,
        "identity": "i",
        "branch": "landed-branch",
        "base": "main",
        "landed": {"state": "yes"},
        "in_flight": False,
        "counted": False,
    }
    commandless = {
        **base,
        "identity": "i",
        "branch": "commandless",
        "base": "main",
        "landed": {"state": "no"},
        "stopped_because": "why",
        "in_flight": False,
        "counted": True,
    }
    out = io.StringIO()
    unpublished._table([landed, commandless], out)
    lines = out.getvalue().splitlines()
    # Split rather than `startswith`: every column is padded to its header's width, so a
    # literal prefix would assert on the width of `IDENTITY` rather than on the row.
    assert any(
        line.split()[:2] == ["i", "landed-branch"] and line.endswith("landed: yes")
        for line in lines
    )
    assert "commandless [i] — why" in lines
    assert not any("land it:" in line for line in lines)
    assert any(
        "or acknowledge:  just unpublished --acknowledge commandless" in line for line in lines
    )
    assert lines[-2] == (
        "1 counted of 2 preserved unpublished branch(es); 0 in flight, 0 acknowledged; "
        "disk not measured (--no-disk)"
    )
