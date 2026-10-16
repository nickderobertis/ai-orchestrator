"""`just unpublished` is the engine's own verb, under this host's directory and drafter.

The recipe is `onepipeline unpublished` reached through `scripts/onepipeline.sh`, and it adds
three things the engine cannot know: the acknowledgement directory every acknowledgement a
manager recorded lives in, `graphs/pr-author.yaml` for every landing command it prints, and
the manager session `scripts/launcher-session.sh` derives from what the harness exports.
Everything else — the targets, the rows, the counting rule, the disk reading, the refusals
and the exit status — is the verb's, passed through untouched. So this journey drives `just
unpublished` as a subprocess, the way a manager does, and asserts what comes back.

Everything is real — the recipe, the wrapper, the engine, `onevcs`, git and the sessions.
What makes it safe is isolation rather than substitution: `ONEVCS_HOME` points at a scratch
registry over a throwaway bare origin, so nothing this host has registered is read or
moved, and `XDG_STATE_HOME` points at a scratch tree, so no acknowledgement this host holds
is read or written.

It lives in the `unpublished-view` project, behind an edge of its own: every test spends
real `onevcs` session verbs, real `git` and a real `just`, so `nx affected` should charge
that to a change of what the journey drives and nothing else. `unpublishedViewWorkspace`
names those files one by one, and `tests/conftest.py` fails a test here that opens a path of
this checkout the key does not name.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from unpublished_registry import ORPHAN_BRANCH, Registry, Session, seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The verb's exit statuses: nothing counted, at least one counted, and refused.
NOTHING_COUNTED, COUNTED, REFUSED = 0, 7, 2

#: The manager session every acknowledgement here is keyed on.
MANAGER = "manager-session-journey"

#: Where this host keeps a manager session's acknowledgements, under the state home.
ACKNOWLEDGEMENTS_UNDER = ("ai-orchestrator", "stop-unfinished", "acknowledged")
DRAFTER = REPO_ROOT / "graphs" / "pr-author.yaml"

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
LAUNCHER_SESSION_SH = REPO_ROOT / "scripts" / "launcher-session.sh"
#: Every environment name the helper reads or exports, as bash spells each one.
HELPER_NAMES = re.compile(r"\$\{([A-Z_]+):-|export ([A-Z_]+)=")


class View(NamedTuple):
    """One `just unpublished` invocation, as a consumer of the recipe sees it."""

    status: int
    stdout: str
    stderr: str

    # `Any` because the document is the engine's own JSON, read back as parsed, indexed by
    # the names its contract declares rather than through a second model of that shape.
    def document(self) -> dict[str, Any]:
        parsed = json.loads(self.stdout)
        assert isinstance(parsed, dict), f"--format json did not emit an object: {self.stdout!r}"
        return parsed

    def rows(self) -> dict[str, dict[str, Any]]:
        return {row["branch"]: row for row in self.document()["rows"]}


class Seeded(NamedTuple):
    """The registry with a closed preserved branch and one a live session holds."""

    registry: Registry
    closed: Session
    held: Session
    owner: subprocess.Popen[bytes]


@pytest.fixture(scope="module")
def seed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Seeded]:
    root = tmp_path_factory.mktemp("unpublished-e2e")
    registry = seeded(root)
    closed = registry.open_session(labels={"run": "run-1", "node": "n", "launcher": MANAGER})
    registry.close_session(closed)
    held = registry.open_session(labels={"run": "run-2", "node": "n", "launcher": MANAGER})
    # llmlint: ignore-block[tests_mirror_real_usage] A session the CLI opens is stale once it
    # exits; `hold` writes onevcs's own two liveness fields so the session reads as one a
    # live engine holds, and the journey reads the installed verb's answer for it.
    owner = registry.hold(held)
    # llmlint: ignore-end[tests_mirror_real_usage]
    # Build output under the live session's worktree: one directory the verb calls out and
    # one it does not.
    (held.worktree / "node_modules").mkdir()
    (held.worktree / "node_modules" / "blob").write_bytes(b"\0" * 300_000)
    (held.worktree / "scratch").mkdir()
    (held.worktree / "scratch" / "notes").write_bytes(b"\0" * 1_000)
    try:
        yield Seeded(registry, closed, held, owner)
    finally:
        owner.kill()
        owner.wait(timeout=e2e_timeout(30))


def _unpublished(
    seed: Seeded,
    *arguments: str,
    state: Path,
    session: str | None = MANAGER,
    identity: Mapping[str, str] | None = None,
    **overrides: str,
) -> View:
    """Run the real `just unpublished` against the scratch registry and state home.

    ``identity`` is what a harness exports, handed through untouched, for the journeys over
    how the helper derives a session from it; ``session`` is the already-derived one.
    """
    environment = {
        name: value
        for name, value in seed.registry.environment.items()
        if name not in IDENTITY_NAMES
    }
    environment.update(identity or {})
    environment["XDG_STATE_HOME"] = str(state)
    if session is not None:
        environment["ONEPIPELINE_LAUNCHER"] = "claude-code"
        environment["ONEPIPELINE_LAUNCHER_SESSION"] = session
    done = subprocess.run(
        ["just", "unpublished", *arguments],
        cwd=REPO_ROOT,
        env={**environment, **overrides},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )
    return View(done.returncode, done.stdout, done.stderr)


def _acknowledgements(state: Path, session: str) -> Path:
    named = hashlib.sha256(session.encode("utf-8")).hexdigest()
    return state.joinpath(*ACKNOWLEDGEMENTS_UNDER) / f"{named}.json"


def test_the_session_target_is_the_harness_session_and_its_landings_draft(
    seed: Seeded, tmp_path: Path
) -> None:
    """With no flag, the session `scripts/launcher-session.sh` establishes; the closed
    branch counts, the held one is in flight, and every landing command drafts."""
    view = _unpublished(seed, state=tmp_path)
    assert view.status == COUNTED, view
    assert seed.closed.branch in view.stdout and seed.held.branch in view.stdout
    assert ORPHAN_BRANCH not in view.stdout, "a branch no session of this manager's names"
    [landing] = [line for line in view.stdout.splitlines() if "land it:" in line]
    assert landing.endswith(f"--pr-author-graph {DRAFTER}"), landing
    assert seed.closed.branch in landing

    as_json = _unpublished(seed, "--format", "json", state=tmp_path)
    assert as_json.status == COUNTED, as_json
    assert as_json.document()["target"] == {"kind": "session", "session": MANAGER}
    rows = as_json.rows()
    assert rows[seed.closed.branch]["counted"] is True
    assert rows[seed.held.branch]["in_flight"] is True
    assert rows[seed.held.branch]["counted"] is False
    assert rows[seed.closed.branch]["land_command"][-2:] == ["--pr-author-graph", str(DRAFTER)]


def test_the_host_target_and_the_disk_reading_are_the_verbs(seed: Seeded, tmp_path: Path) -> None:
    view = _unpublished(seed, "--host", "--disk", "--format", "json", state=tmp_path)
    assert view.status == COUNTED, view
    assert view.document()["target"] == {"kind": "host"}
    rows = view.rows()
    assert {seed.closed.branch, seed.held.branch, ORPHAN_BRANCH} <= set(rows)
    disk = rows[seed.held.branch]["disk"]
    assert disk["run_root"] == str(seed.held.run_root), disk
    assert disk["build_output"]["node_modules"] >= 300_000, disk
    assert "scratch" not in disk["build_output"], "only the build outputs are called out"
    without = _unpublished(seed, "--host", "--format", "json", state=tmp_path)
    assert all(row["disk"] is None for row in without.rows().values())


def test_an_empty_host_inventory_exits_nothing_counted(seed: Seeded, tmp_path: Path) -> None:
    home = tmp_path / "empty-onevcs"
    home.mkdir()
    view = _unpublished(seed, "--host", "--format", "json", state=tmp_path, ONEVCS_HOME=str(home))
    assert view.status == NOTHING_COUNTED, view
    assert view.document()["rows"] == []


def test_an_acknowledgement_lands_in_this_hosts_directory_and_is_the_sessions_alone(
    seed: Seeded, tmp_path: Path
) -> None:
    receipt = _unpublished(
        seed, "--acknowledge", seed.closed.branch, "--reason", "asking the user", state=tmp_path
    )
    assert receipt.status == NOTHING_COUNTED, receipt
    written = json.loads(_acknowledgements(tmp_path, MANAGER).read_text(encoding="utf-8"))
    assert written["version"] == 1
    [entry] = written["acknowledged"]
    tip = seed.registry.run(
        "git", "-C", str(seed.registry.checkout), "rev-parse", seed.closed.branch
    )
    assert (entry["branch"], entry["tip"], entry["reason"]) == (
        seed.closed.branch,
        tip.stdout.strip(),
        "asking the user",
    )
    mine = _unpublished(seed, "--format", "json", state=tmp_path).rows()[seed.closed.branch]
    assert mine["counted"] is False and mine["acknowledgement"]["reason"] == "asking the user"
    other = _unpublished(
        seed, "--host", "--format", "json", state=tmp_path, session="manager-session-other"
    )
    assert other.rows()[seed.closed.branch]["acknowledgement"] is None


def test_an_acknowledgement_the_verb_refuses_writes_nothing(seed: Seeded, tmp_path: Path) -> None:
    for arguments in (
        ("--acknowledge", seed.closed.branch, "--reason", "   "),
        ("--acknowledge", "claude/no-such-branch", "--reason", "kept"),
    ):
        refused = _unpublished(seed, *arguments, state=tmp_path)
        assert refused.status == REFUSED, refused
    unidentified = _unpublished(
        seed, "--acknowledge", seed.closed.branch, "--reason", "kept", state=tmp_path, session=None
    )
    assert unidentified.status == REFUSED, unidentified
    assert not tmp_path.joinpath(*ACKNOWLEDGEMENTS_UNDER).exists()


def test_the_names_cleared_are_every_name_the_identity_helper_reads() -> None:
    """`IDENTITY_NAMES` restates the helper's inputs, so it is read back against them."""
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
        pytest.param({"CODEX_THREAD_ID": "codex-1"}, "codex-1", None, id="codex"),
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
    """The recipe derives the manager session itself, from what the harness exports."""
    receipt = _unpublished(
        seed,
        "--acknowledge",
        ORPHAN_BRANCH,
        "--reason",
        "keyed on the harness",
        state=tmp_path,
        session=None,
        identity=exported,
    )
    assert receipt.status in (NOTHING_COUNTED, COUNTED), receipt
    assert _acknowledgements(tmp_path, keyed_on).is_file()
    if passed_over is not None:
        assert not _acknowledgements(tmp_path, passed_over).exists()
