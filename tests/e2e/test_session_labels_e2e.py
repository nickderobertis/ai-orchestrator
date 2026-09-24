"""The adopted `onevcs` stores the labels a session is opened with, and filters on them.

The engine stamps `run`, `node` and `launcher` labels on every session a node opens, so
a listing of preserved branches can identify the opening session without joining a
run's journal. That requires the `onevcs` this checkout's `.venv` resolves to store labels given at
`session open`, answers them back on `recoverable` and `session holders`, and filters by
`--label` and `--session` — which is what this journey drives, on the pinned CLI, over a
scratch registry. `tests/e2e/test_repositories_field_dispatch_e2e.py` holds the other
half: that the installed engine stamps the three labels on the session a dispatch opens.

Two sessions are opened under different launchers, each commits and closes without
publishing, so each leaves a preserved branch; a filter that answered both, or neither,
reads the same as one that ignored its argument unless both exist.

The identity is a scratch one against a scratch `ONEVCS_HOME`, seeded and registered
through the real recipe, because a `session open` reclaims run roots on the way in and
the real registry's run roots are live dispatches.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] Placed
beside `tests/e2e/test_worktree_pool_e2e.py`, which drives the same installed `onevcs`
through the same recipe from this project; a project of this journey's own would be a
change to `nx.json`, `orchestrator/project.json` and `tests/nx_inputs.py`, which is the
module docstring of `tests/e2e/test_repo_registry_apply_e2e.py`'s standing follow-up
rather than this journey's to make.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] The same edge, for the
same reason: the cost class is a seeded identity, a registry apply and two real `onevcs`
sessions, and the project it would sit behind is that deferred follow-up.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict

import pytest
from conftest import git
from scratch_identity import GIT_IDENTITY, Identity, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

EXECUTION_ALIAS = "execution"

#: The labels each session is opened with: the three keys the engine stamps, with a
#: launcher of its own per session so the `launcher` filter has one row to pick.
FIRST_LABELS = {"run": "labels-run", "node": "first", "launcher": "launcher-one"}
SECOND_LABELS = {"run": "labels-run", "node": "second", "launcher": "launcher-two"}


#: A session's token as `onevcs` mints it, which every listing here names a session by.
SessionToken = NewType("SessionToken", str)


class Session(NamedTuple):
    """One really-opened session, as `session open` reported it."""

    token: SessionToken
    worktree: Path
    labels: dict[str, str]


# The keys of `recoverable --json` and `session holders --json` this journey reads, and
# only those: both documents are `onevcs`'s contract, and restating the whole of either
# here would be a second schema nothing reconciles.
class RecoverableRow(TypedDict):
    """One preserved branch, as `recoverable --json` reports it."""

    session: SessionToken
    labels: dict[str, str]


class Holder(TypedDict):
    """One recorded session, as `session holders --json` reports it."""

    token: SessionToken
    labels: dict[str, str]


def _onevcs(identity: Identity, *arguments: str) -> subprocess.CompletedProcess[str]:
    """The pinned CLI, against the scratch registry."""
    return subprocess.run(
        ["uv", "run", "onevcs", *arguments],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "ONEVCS_HOME": str(identity.home),
            **identity.environment,
        },
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _open(identity: Identity, labels: dict[str, str]) -> Session:
    """Open a session carrying ``labels``, each passed as the `--label` a caller types."""
    flags = [flag for key, value in labels.items() for flag in ("--label", f"{key}={value}")]
    opened = _onevcs(identity, "session", "open", EXECUTION_ALIAS, *flags)
    assert opened.returncode == 0, f"session open failed:\n{opened.stdout}\n{opened.stderr}"
    reported = json.loads(opened.stdout.strip())
    return Session(
        token=SessionToken(reported["token"]), worktree=Path(reported["worktree"]), labels=labels
    )


def _commit(session: Session) -> None:
    """Leave one commit on the session's branch, so its close preserves the branch."""
    (session.worktree / f"{session.labels['node']}.md").write_text("work\n", encoding="utf-8")
    git("add", "-A", cwd=session.worktree)
    git(*GIT_IDENTITY, "commit", "-qm", "feat: leave work behind", cwd=session.worktree)


def _close(identity: Identity, session: Session) -> None:
    closed = _onevcs(identity, "session", "close", session.token)
    assert closed.returncode == 0, f"session close failed:\n{closed.stdout}\n{closed.stderr}"


def _recoverable(identity: Identity, *filters: str) -> list[RecoverableRow]:
    listed = _onevcs(identity, "recoverable", "--json", *filters)
    assert listed.returncode == 0, (
        f"recoverable --json {' '.join(filters)} exited {listed.returncode}:\n"
        f"{listed.stdout}\n{listed.stderr}"
    )
    rows: list[RecoverableRow] = json.loads(listed.stdout)
    return rows


class Preserved(NamedTuple):
    """Two sessions, each closed over a preserved branch, in one scratch registry."""

    identity: Identity
    first: Session
    second: Session
    holders_while_open: list[Holder]


@pytest.fixture
def preserved(tmp_path: Path) -> Preserved:
    """Open two sessions under different launchers, commit in each, and close both."""
    identity = seeded(tmp_path)
    first = _open(identity, FIRST_LABELS)
    second = _open(identity, SECOND_LABELS)
    for session in (first, second):
        _commit(session)
    held = _onevcs(identity, "session", "holders", EXECUTION_ALIAS, "--json")
    assert held.returncode == 0, f"session holders failed:\n{held.stdout}\n{held.stderr}"
    holders: list[Holder] = json.loads(held.stdout)
    for session in (first, second):
        _close(identity, session)
    return Preserved(identity, first, second, holders)


def test_a_launcher_label_answers_that_launchers_preserved_branch_and_no_other(
    preserved: Preserved,
) -> None:
    """`--label launcher=<one>` is one row: the matching session's, with its labels."""
    rows = _recoverable(
        preserved.identity, "--label", f"launcher={preserved.first.labels['launcher']}"
    )

    assert [row["session"] for row in rows] == [preserved.first.token], (
        f"`recoverable --label launcher={preserved.first.labels['launcher']}` answered "
        f"{rows}, not exactly the row of session {preserved.first.token} — the other "
        f"session, {preserved.second.token}, carries a different launcher"
    )
    assert rows[0]["labels"] == preserved.first.labels, (
        f"the row names the labels {rows[0]['labels']}, not the {preserved.first.labels} "
        "the session was opened with"
    )


def test_a_session_token_answers_that_sessions_row_alone(preserved: Preserved) -> None:
    """`--session <token>` is the row that session left, and nothing else."""
    rows = _recoverable(preserved.identity, "--session", preserved.second.token)

    assert [(row["session"], row["labels"]) for row in rows] == [
        (preserved.second.token, preserved.second.labels)
    ], f"`recoverable --session {preserved.second.token}` answered {rows}"


def test_a_label_no_session_carries_answers_an_empty_report(preserved: Preserved) -> None:
    """A filter matching nothing is an empty report and a success, never a refusal.

    The `Stop` hook asks this at the end of every turn, so the common answer — this
    launcher left nothing behind — has to read as nothing rather than as an error.
    Both branches exist, so an empty answer here is the filter's and not the host's.
    """
    everything = _recoverable(preserved.identity)
    assert {row["session"] for row in everything} == {
        preserved.first.token,
        preserved.second.token,
    }, f"an unfiltered `recoverable --json` should list both preserved branches: {everything}"

    assert _recoverable(preserved.identity, "--label", "launcher=nobody-launched-this") == []


def test_session_holders_carries_each_holders_labels_as_given(preserved: Preserved) -> None:
    """Every holder the listing names carries the labels its session was opened with."""
    by_token = {holder["token"]: holder["labels"] for holder in preserved.holders_while_open}

    assert by_token == {
        preserved.first.token: preserved.first.labels,
        preserved.second.token: preserved.second.labels,
    }, f"`session holders --json` answered {preserved.holders_while_open}"


def test_a_filtered_read_answers_over_an_excluded_branch_git_cannot_read(
    preserved: Preserved,
) -> None:
    """The filter narrows what is read, so an excluded branch cannot fail the read.

    The excluded session's branch is left with a tip commit whose tree is gone, so any
    read that decides that branch fails naming the tree. A filter applied only to the
    printed rows would still decide it and fail the same way; one that never reads the
    branch answers the matching row. The unfiltered read and a filter selecting the
    broken branch are the controls that the corruption is reachable at all.
    """
    execution = preserved.identity.execution
    excluded_branch = f"onevcs/{preserved.second.token}"
    tree = git("rev-parse", f"{excluded_branch}^{{tree}}", cwd=execution).strip()
    # llmlint: ignore-block[tests_mirror_real_usage] The unreadable branch is the fixture,
    # not the subject: the task asks for the filter's narrowing to be shown over a registry
    # whose excluded sessions cannot be read, no `onevcs` or git verb makes a branch
    # unreadable, and every read below goes through the public `recoverable` CLI.
    loose = execution / ".git" / "objects" / tree[:2] / tree[2:]
    assert loose.is_file(), f"the excluded branch's tree {tree} is not a loose object at {loose}"
    loose.chmod(0o644)
    loose.unlink()
    # llmlint: ignore-end[tests_mirror_real_usage]

    for selecting in ([], ["--label", f"launcher={preserved.second.labels['launcher']}"]):
        broken = _onevcs(preserved.identity, "recoverable", "--json", *selecting)
        assert broken.returncode != 0 and tree in broken.stderr, (
            f"`recoverable --json {' '.join(selecting)}` should fail on the unreadable "
            f"tree {tree}, and exited {broken.returncode}:\n{broken.stdout}\n{broken.stderr}"
        )

    rows = _recoverable(
        preserved.identity, "--label", f"launcher={preserved.first.labels['launcher']}"
    )
    assert [row["session"] for row in rows] == [preserved.first.token], (
        f"the filtered read answered {rows}, not session {preserved.first.token}'s row alone"
    )
