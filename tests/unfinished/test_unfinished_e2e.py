"""The `Stop` hook, `just unpublished` and `just unfinished` answer what a manager session owes.

A manager's turn should not end with a run nothing is watching, and it should not end with a
branch its runs left preserved that nobody landed or deliberately left. Both questions are
the engine's: `onepipeline unwatched`, and `onepipeline unpublished`, which decides whether a
manager session owes a preserved branch from the session labels the engine stamps on every
session it opens. The `Stop` hook `.claude/settings.json` registers is the engine's
`onepipeline stop-guard --unpublished` alone; `just unpublished` is the engine verb passed
through; `just unfinished` stacks the two halves. These journeys drive all three as their
callers do — the hook command exactly as the settings file declares it, run through `sh -c`
with Claude Code's Stop payload on standard input, and each recipe through `just` — over a
scratch `onevcs` registry of **real labelled sessions** (`session open --label run=…
--label node=… --label launcher=…`, which is what the engine passes for every session a
node opens), real commits and real landings, and a runs root of run records
`tests/e2e/probe_run_root.py` builds, which `tests/test_engine_contracts.py` holds field by
field to the installed engine.

Nothing is doubled on these paths: the hook command, the recipes, the wrapper, the engine,
`onevcs`, git and the sessions are real. What makes it safe is isolation — `ONEVCS_HOME`,
`ONEPIPELINE_RUNS_DIR` and `XDG_STATE_HOME` each point at a scratch tree — so nothing this
host has registered, launched or acknowledged is read or moved.

These journeys are the `unfinished` project's, behind an Nx edge of their own: each spends
real `onevcs` sessions, real `git` and a real `just` per assertion, so `unfinishedWorkspace`
names the files they read and no tree, and a change to another tier does not run them.

llmlint: ignore-file[tests_mirror_real_usage] Two states no interface produces are arranged:
a session held by a live owner, and a closed session record older than the sweep's floor.
`onevcs session open` records the pid of a process that has exited by the time it prints, so
a live session exists only while the engine holds it, which this suite may not run;
`tests/unpublished_registry.py`'s `hold` points the record's owner at a process the journey
controls, the same two fields `onevcs` reads. And a record is aged by moving its files'
modification times back, which is what four hours of waiting would leave.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import budget_telemetry
import probe_run_root
import pytest
from unpublished_registry import BASE, Registry, Session, commit_on, git, seeded
from waits import timeout as e2e_timeout

from orchestrator import npm_installs
from orchestrator.root import REPO_ROOT

ONEPIPELINE = npm_installs.ENGINE
ONEVCS = REPO_ROOT / ".venv" / "bin" / "onevcs"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
#: The drafter graph every landing command the engine prints has to run.
DRAFTER = REPO_ROOT / "graphs" / "pr-author.yaml"
#: Where this host keeps a manager session's acknowledgements, under the state home: the
#: directory the hook and the recipe both name.
ACKNOWLEDGEMENTS_UNDER = ("ai-orchestrator", "stop-unfinished", "acknowledged")

#: The statuses `just unfinished` answers, as `orchestrator/unfinished.py` declares them.
NOTHING_OWED, UNWATCHED, UNPUBLISHED, BOTH, UNANSWERED, REFUSED = 0, 6, 7, 8, 1, 2

#: One manager session per thing owed, so each journey reads its own answer.
OWES_NO = "manager-owes-an-unlanded-branch"
OWES_UNKNOWN = "manager-owes-a-may-have-landed-branch"
OWES_IN_PART = "manager-owes-a-partly-landed-branch"
SETTLED = "manager-landed-in-flight-or-kept"
ACKNOWLEDGES = "manager-acknowledges"
VERSION_ONE = "manager-acknowledged-before-adoption"
OWES_BOTH = "manager-owes-both"
OWES_A_WATCH = "manager-owes-a-watch"
OTHER_MANAGER = "manager-somebody-else"
OWES_A_SUPERSEDED_BRANCH = "manager-owes-a-superseded-branch"
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

#: The budget the session read's wall clock at scale is recorded under: the read the
#: `Stop` hook and a manager wait through before a turn may end.
OWN_READ_BUDGET = "own-sessions-read-seconds"


class World(NamedTuple):
    """One scratch registry and runs root, and the sessions each manager's runs opened."""

    registry: Registry
    runs: Path
    unlanded: Session
    unknown: Session
    in_part: Session
    landed: Session
    held: Session
    kept: Session
    acknowledged: Session
    version_one: Session
    both: Session
    other: Session
    superseded: Session
    owner: subprocess.Popen[bytes]


def _labels(run: str, launcher: str) -> dict[str, str]:
    return {"run": run, "node": "node-1", "launcher": launcher}


def _closed(registry: Registry, run: str, launcher: str) -> Session:
    session = registry.open_session(labels=_labels(run, launcher))
    registry.close_session(session)
    return session


def _publish(registry: Registry, session: Session) -> None:
    """Land ``session``'s branch through the real recipe, as `local-direct` lands one."""
    published = registry.run(
        "just", "publish-branch", session.branch, "--repo", str(registry.checkout), "--no-draft"
    )
    assert published.returncode == 0, published.stderr + published.stdout
    git("checkout", "-q", BASE, cwd=registry.checkout)
    git("pull", "-q", "--ff-only", cwd=registry.checkout)


def _may_have_landed(registry: Registry, run: str, launcher: str) -> Session:
    """A closed session whose landing onevcs cannot decide: `landed` `unknown`.

    The base takes a change under the very subject a landing of this branch would carry,
    without carrying what the branch changed — what a landing nobody recorded and then
    somebody edited over looks like, and which `onevcs` answers undecidable rather than
    `no`.
    """
    session = _closed(registry, run, launcher)
    git("checkout", "-q", BASE, cwd=registry.checkout)
    (registry.checkout / f"elsewhere-{session.token}.txt").write_text("x\n", encoding="utf-8")
    git("add", "-A", cwd=registry.checkout)
    git("commit", "-q", "-m", f"feat: work-{session.token}.txt", cwd=registry.checkout)
    git("push", "-q", "origin", BASE, cwd=registry.checkout)
    return session


def _landed_in_part(registry: Registry, run: str, launcher: str) -> Session:
    """A closed session whose branch landed and then gained a commit: `landed` `in-part`."""
    session = _closed(registry, run, launcher)
    _publish(registry, session)
    commit_on(registry.checkout, session.branch, f"more-{session.token}.txt")
    return session


def _superseded(registry: Registry, run: str, launcher: str) -> Session:
    """A closed session whose branch a retry replaced and landed, recorded as onevcs records it.

    The retry's version of the session's one file lands on the base, so the branch still
    differs there, and `onevcs supersede` — the verb the engine calls when a retry lands —
    records the pair.
    """
    session = _closed(registry, run, launcher)
    name = f"work-{session.token}.txt"
    git("checkout", "-q", BASE, cwd=registry.checkout)
    (registry.checkout / name).write_text("the retry's version\n", encoding="utf-8")
    git("add", "-A", cwd=registry.checkout)
    git("commit", "-q", "-m", f"feat: land the retry of {name}", cwd=registry.checkout)
    git("push", "-q", "origin", BASE, cwd=registry.checkout)
    landing = git("rev-parse", "HEAD", cwd=registry.checkout).strip()
    registry.onevcs(
        "supersede",
        session.branch,
        "--repo",
        str(registry.checkout),
        "--by",
        "claude/the-retry",
        "--landing",
        landing,
    )
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
    unlanded = _closed(registry, "run-unlanded", OWES_NO)
    unknown = _may_have_landed(registry, "run-unknown", OWES_UNKNOWN)
    in_part = _landed_in_part(registry, "run-in-part", OWES_IN_PART)
    landed = _closed(registry, "run-landed", SETTLED)
    _publish(registry, landed)
    kept = _closed(registry, "run-kept", SETTLED)
    acknowledged = _closed(registry, "run-ack", ACKNOWLEDGES)
    version_one = _closed(registry, "run-v1", VERSION_ONE)
    both = _closed(registry, "run-both", OWES_BOTH)
    other = _closed(registry, "run-other", OTHER_MANAGER)
    superseded = _superseded(registry, "run-superseded", OWES_A_SUPERSEDED_BRANCH)
    held = registry.open_session(labels=_labels("run-held", SETTLED))
    owner = registry.hold(held)
    runs = root / "runs"
    runs.mkdir()
    _unwatched_run(runs, "run-both", OWES_BOTH)
    _unwatched_run(runs, "run-watch", OWES_A_WATCH)
    try:
        yield World(
            registry,
            runs,
            unlanded,
            unknown,
            in_part,
            landed,
            held,
            kept,
            acknowledged,
            version_one,
            both,
            other,
            superseded,
            owner,
        )
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

    # `Any` because the object is the recipe's own JSON read back as parsed: the engine's
    # document, which the tests index by the names its contract declares rather than
    # through a second model of that shape.
    def document(self) -> dict[str, Any]:
        parsed = json.loads(self.stdout)
        assert isinstance(parsed, dict), self.stdout
        return parsed


def _environment(
    registry: Registry, runs: Path, state: Path, launcher: str | None
) -> dict[str, str]:
    environment = {k: v for k, v in registry.environment.items() if k not in IDENTITY_NAMES}
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
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
    return _recipe(
        world.registry, world.runs, recipe, *arguments, state=state, launcher=launcher, **overrides
    )


def _recipe(
    registry: Registry,
    runs: Path,
    recipe: str,
    *arguments: str,
    state: Path,
    launcher: str | None = None,
    **overrides: str,
) -> Answer:
    done = subprocess.run(  # noqa: S603 - the real recipe, run as an operator runs it
        ["just", recipe, *arguments],
        cwd=REPO_ROOT,
        env={**_environment(registry, runs, state, launcher), **overrides},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    return Answer(done.returncode, done.stdout, done.stderr)


def _listing(world: World, state: Path, session: str, **overrides: str) -> Answer:
    """`just unpublished --session <session> --format json`, the engine's whole document."""
    return _just(
        world, "unpublished", "--session", session, "--format", "json", state=state, **overrides
    )


def _acknowledge(
    registry: Registry, runs: Path, state: Path, launcher: str, branch: str, reason: str
) -> Answer:
    """Acknowledge ``branch`` through the real recipe, as the manager ``launcher``."""
    receipt = _recipe(
        registry,
        runs,
        "unpublished",
        "--acknowledge",
        branch,
        "--reason",
        reason,
        state=state,
        launcher=launcher,
    )
    assert receipt.status in (NOTHING_OWED, UNPUBLISHED), receipt
    return receipt


class Registered(NamedTuple):
    """The one `Stop` command `.claude/settings.json` registers, and its bound."""

    command: str
    timeout: int


def _registered() -> Registered:
    """The `Stop` entry out of the tracked settings file, which is what the harness runs."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    commands = [
        Registered(str(hook["command"]), int(hook["timeout"]))
        for matcher in settings["hooks"]["Stop"]
        for hook in matcher["hooks"]
        if hook["type"] == "command"
    ]
    assert len(commands) == 1, f"{SETTINGS} registers {len(commands)} Stop commands: {commands}"
    return commands[0]


def _hook(
    registry: Registry,
    runs: Path,
    state: Path,
    session: str,
    *,
    again: bool = False,
    launcher: str | None = None,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    """Run the registered `Stop` command as the harness runs it: `sh -c`, a payload on stdin.

    `CLAUDE_PROJECT_DIR` is this checkout, which the command resolves its engine and its
    drafter graph against. No launcher session is exported unless ``launcher`` names one,
    because the payload names the session and the hook must not fall through to whoever the
    process runs as.
    """
    registered = _registered()
    payload = {
        "session_id": session,
        "transcript_path": "/dev/null",
        "hook_event_name": "Stop",
        "stop_hook_active": again,
    }
    return subprocess.run(  # noqa: S603 - the tracked hook command, run as the harness runs it
        ["/bin/sh", "-c", registered.command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        env={
            **_environment(registry, runs, state, launcher),
            "CLAUDE_PROJECT_DIR": str(REPO_ROOT),
            **overrides,
        },
        cwd=str(REPO_ROOT),
        timeout=e2e_timeout(registered.timeout),
    )


def _stop(
    world: World,
    state: Path,
    session: str,
    *,
    again: bool = False,
    launcher: str | None = None,
    **overrides: str,
) -> subprocess.CompletedProcess[str]:
    return _hook(
        world.registry, world.runs, state, session, again=again, launcher=launcher, **overrides
    )


def _refused(answered: subprocess.CompletedProcess[str]) -> str:
    """The reason a hook that refused the turn gave, having held the whole shape first."""
    assert answered.returncode == 0, f"the hook exited {answered.returncode}: {answered.stderr}"
    decision = json.loads(answered.stdout)
    assert decision.get("decision") == "block", f"the hook did not block: {answered.stdout!r}"
    reason = decision["reason"]
    assert isinstance(reason, str) and reason.strip()
    return str(reason)


def _let_end(answered: subprocess.CompletedProcess[str]) -> None:
    """The silent ending: exit `0` and nothing on standard output, so the turn ends."""
    assert answered.returncode == 0, f"the hook exited {answered.returncode}: {answered.stderr}"
    assert answered.stdout == "", f"the hook said something: {answered.stdout!r}"


def _landing_commands(reason: str) -> list[str]:
    """Every landing command a listing prints, each on its `land it:` line."""
    return [
        line.split("land it:", 1)[1].strip()
        for line in reason.splitlines()
        if line.strip().startswith("land it:")
    ]


def _assert_every_landing_drafts(reason: str) -> None:
    commands = _landing_commands(reason)
    assert commands, f"the listing printed no landing command:\n{reason}"
    for command in commands:
        assert command.startswith("onepipeline "), command
        assert command.endswith(f"--pr-author-graph {DRAFTER}"), (
            f"a landing command the listing printed does not run this repository's drafter "
            f"graph: {command}"
        )


@pytest.mark.parametrize(
    ("session", "landed"),
    [
        pytest.param(OWES_NO, "no", id="unlanded"),
        pytest.param(OWES_UNKNOWN, "unknown", id="may-have-landed"),
        pytest.param(OWES_IN_PART, "in-part", id="landed-in-part"),
    ],
)
def test_the_hook_blocks_naming_a_branch_the_session_owes(
    world: World, tmp_path: Path, session: str, landed: str
) -> None:
    """One owed branch per landing state the engine counts, read the same by all three."""
    branch = {OWES_NO: world.unlanded, OWES_UNKNOWN: world.unknown, OWES_IN_PART: world.in_part}[
        session
    ].branch
    listed = _listing(world, tmp_path / "view", session)
    assert listed.status == UNPUBLISHED, listed
    [row] = listed.document()["rows"]
    assert (row["branch"], row["landed"]["state"], row["counted"]) == (branch, landed, True)

    reason = _refused(_stop(world, tmp_path / "hook", session))
    assert branch in reason, reason
    assert world.other.branch not in reason, "another manager's branch is never this one's"
    _assert_every_landing_drafts(reason)

    owed = _just(world, "unfinished", "--session", session, state=tmp_path / "view")
    assert owed.status == UNPUBLISHED, owed
    assert branch in owed.halves()[1]


def test_the_hook_lets_a_session_end_whose_branches_are_landed_in_flight_or_acknowledged(
    world: World, tmp_path: Path
) -> None:
    """Landed is never listed, in flight is listed and never counted, and the one branch
    left is owed until it is acknowledged at its tip — then the stop goes through."""
    state = tmp_path / "state"
    before = _listing(world, state, SETTLED)
    assert before.status == UNPUBLISHED, before
    rows = {row["branch"]: row for row in before.document()["rows"]}
    assert world.landed.branch not in rows, "a landed branch is not listed at all"
    assert rows[world.held.branch]["in_flight"] is True
    assert rows[world.held.branch]["counted"] is False
    assert rows[world.kept.branch]["counted"] is True
    assert world.kept.branch in _refused(_stop(world, state, SETTLED))

    _acknowledge(world.registry, world.runs, state, SETTLED, world.kept.branch, "asking the user")

    after = _listing(world, state, SETTLED)
    assert after.status == NOTHING_OWED, after
    kept = {row["branch"]: row for row in after.document()["rows"]}[world.kept.branch]
    assert kept["counted"] is False and kept["acknowledgement"]["reason"] == "asking the user"
    _let_end(_stop(world, state, SETTLED))
    assert _just(world, "unfinished", "--session", SETTLED, state=state).status == NOTHING_OWED


def test_an_acknowledged_branch_that_gains_a_commit_blocks_again(
    world: World, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    assert world.acknowledged.branch in _refused(_stop(world, state, ACKNOWLEDGES))
    _acknowledge(world.registry, world.runs, state, ACKNOWLEDGES, world.acknowledged.branch, "kept")
    _let_end(_stop(world, state, ACKNOWLEDGES))
    assert _just(world, "unfinished", "--session", ACKNOWLEDGES, state=state).status == 0

    commit_on(world.registry.checkout, world.acknowledged.branch, "moved.txt")
    moved = _refused(_stop(world, state, ACKNOWLEDGES))
    assert world.acknowledged.branch in moved, "a branch past its acknowledgement is owed"
    assert _listing(world, state, ACKNOWLEDGES).status == UNPUBLISHED
    owed = _just(world, "unfinished", "--session", ACKNOWLEDGES, state=state)
    assert owed.status == UNPUBLISHED, owed


def test_an_acknowledgement_written_before_adoption_still_stands(
    world: World, tmp_path: Path
) -> None:
    """A version-1 file exactly as this host's retired writer left one, read by the engine.

    `orchestrator/unpublished.py` wrote `<state>/ai-orchestrator/stop-unfinished/
    acknowledged/<sha256 of the session>.json` as `{"version": 1, "acknowledged": [{branch,
    identity, tip, reason, at}]}`, indented two spaces with a trailing newline. Every
    acknowledgement a manager recorded before the engine took the decision over is that
    file, so the hook and both recipes have to honour it untouched.
    """
    state = tmp_path / "state"
    tip = git("rev-parse", world.version_one.branch, cwd=world.registry.checkout).strip()
    entry = {
        "branch": world.version_one.branch,
        "identity": world.registry.identity,
        "tip": tip,
        "reason": "kept for the user's review",
        "at": "2026-10-01T12:00:00Z",
    }
    directory = state.joinpath(*ACKNOWLEDGEMENTS_UNDER)
    directory.mkdir(parents=True)
    file = directory / f"{hashlib.sha256(VERSION_ONE.encode('utf-8')).hexdigest()}.json"
    written = json.dumps({"version": 1, "acknowledged": [entry]}, indent=2) + "\n"
    file.write_text(written, encoding="utf-8")

    _let_end(_stop(world, state, VERSION_ONE))
    listed = _listing(world, state, VERSION_ONE)
    assert listed.status == NOTHING_OWED, listed
    [row] = listed.document()["rows"]
    assert row["acknowledgement"] == entry, row
    assert _just(world, "unfinished", "--session", VERSION_ONE, state=state).status == 0
    assert file.read_text(encoding="utf-8") == written, "a read never rewrites the record"


def test_an_unreadable_answer_blocks_and_is_never_nothing_owed(
    world: World, tmp_path: Path
) -> None:
    """A registry `onevcs` cannot read: the hook refuses saying why, and neither recipe
    answers nothing owed."""
    broken = tmp_path / "broken-home"
    broken.mkdir()
    (broken / "registry.json").write_text("{not json", encoding="utf-8")
    state = tmp_path / "state"
    reason = _refused(_stop(world, state, WORKER, ONEVCS_HOME=str(broken)))
    assert "unanswered" in reason and str(broken / "registry.json") in reason, reason

    listed = _listing(world, state, OWES_A_WATCH, ONEVCS_HOME=str(broken))
    assert listed.status == UNANSWERED, listed
    owed = _just(
        world, "unfinished", "--session", OWES_A_WATCH, state=state, ONEVCS_HOME=str(broken)
    )
    assert owed.status == UNANSWERED, owed
    assert "the unpublished half could not answer" in owed.stderr
    assert "run-watch" in owed.stdout, "the half that answered is still shown"


def test_a_superseded_branch_is_owed_with_its_reclaim(world: World, tmp_path: Path) -> None:
    """Still owed — the counting rule is unchanged — and offered onevcs's reclaim first."""
    reason = _refused(_stop(world, tmp_path / "state", OWES_A_SUPERSEDED_BRANCH))
    assert world.superseded.branch in reason
    assert f"onevcs reclaim {world.superseded.branch}" in reason, reason
    owed = _just(world, "unfinished", "--session", OWES_A_SUPERSEDED_BRANCH, state=tmp_path)
    assert owed.status == UNPUBLISHED, owed


@pytest.mark.parametrize(
    ("session", "status"),
    [
        pytest.param(OWES_A_WATCH, UNWATCHED, id="unwatched-run"),
        pytest.param(OWES_NO, UNPUBLISHED, id="unpublished-branch"),
        pytest.param(OWES_BOTH, BOTH, id="both"),
        pytest.param(WORKER, NOTHING_OWED, id="neither"),
    ],
)
def test_the_hook_answers_what_just_unfinished_says_the_session_owes(
    world: World, tmp_path: Path, session: str, status: int
) -> None:
    """One verdict per state, and the view's own answer beside it."""
    owed = _just(world, "unfinished", "--session", session, "--json", state=tmp_path / "view")
    assert owed.status == status, owed
    document = owed.document()
    unwatched = document["unwatched"]
    branches = [row["branch"] for row in document["unpublished"]["rows"] if row["counted"]]
    answered = _stop(world, tmp_path / "hook", session)
    if status == NOTHING_OWED:
        _let_end(answered)
        return
    reason = _refused(answered)
    for line in unwatched:
        assert reason.count(line) == 1, f"{line!r} is not in the verdict exactly once:\n{reason}"
    for branch in branches:
        assert f"onepipeline unpublished --acknowledge {branch} " in reason, reason
    assert ("onepipeline watch" in reason) == bool(unwatched), reason


def test_a_continuation_over_an_unchanged_verdict_stands_aside(
    world: World, tmp_path: Path
) -> None:
    """Told once about both conditions, the manager's continuation ends the turn."""
    state = tmp_path / "state"
    first = _refused(_stop(world, state, OWES_BOTH))
    assert "run-both" in first and world.both.branch in first
    _let_end(_stop(world, state, OWES_BOTH, again=True))


def test_a_worker_whose_own_session_owns_nothing_is_let_end(world: World, tmp_path: Path) -> None:
    """A dispatched worker inherits its manager's launcher session; its own payload owns none."""
    _let_end(_stop(world, tmp_path, WORKER, launcher=OWES_BOTH))
    answer = _just(world, "unfinished", "--session", WORKER, state=tmp_path, launcher=OWES_BOTH)
    assert answer.status == NOTHING_OWED, answer


def test_the_recipes_default_to_the_session_the_harness_names(world: World, tmp_path: Path) -> None:
    listed = _just(world, "unpublished", state=tmp_path, launcher=OWES_NO)
    assert listed.status == UNPUBLISHED and world.unlanded.branch in listed.stdout, listed
    assert _just(world, "unfinished", state=tmp_path, launcher=OWES_NO).status == UNPUBLISHED
    refused = _just(world, "unfinished", state=tmp_path)
    assert (
        refused.status == REFUSED and "no manager session identifies this shell" in refused.stderr
    )
    malformed = _just(world, "unfinished", "--session", "not a session!", state=tmp_path)
    assert malformed.status == REFUSED and malformed.stdout == "", malformed
    assert "'not a session!' is not the shape a session id has" in malformed.stderr


def test_the_recipe_passes_the_verbs_targets_and_refusals_through(
    world: World, tmp_path: Path
) -> None:
    """`--host`, `--token` and `--disk` are the verb's, and so is a refusal's `2`."""
    host = _just(world, "unpublished", "--host", "--format", "json", state=tmp_path)
    assert host.status == UNPUBLISHED, host
    assert host.document()["target"] == {"kind": "host"}
    listed = {row["branch"] for row in host.document()["rows"]}
    assert {world.unlanded.branch, world.other.branch} <= listed

    token = _just(
        world,
        "unpublished",
        "--token",
        world.other.token,
        "--disk",
        "--format",
        "json",
        state=tmp_path,
    )
    assert token.status == UNPUBLISHED, token
    [row] = token.document()["rows"]
    assert row["branch"] == world.other.branch
    assert row["disk"]["run_root"] == str(world.other.run_root), row["disk"]

    refused = _just(
        world,
        "unpublished",
        "--acknowledge",
        world.other.branch,
        state=tmp_path,
        launcher=OTHER_MANAGER,
    )
    assert refused.status == REFUSED, refused
    assert not tmp_path.joinpath(*ACKNOWLEDGEMENTS_UNDER).exists(), "a refusal writes nothing"


def test_a_preserved_branch_is_still_owed_after_its_aged_record_is_swept(
    tmp_path: Path,
) -> None:
    """A closed session whose branch is preserved on its origin, its record older than the
    sweep's floor: `just sweep` keeps the record and its labels, and the branch stays owed
    by the recipe and the hook until it is acknowledged at its tip — and again once it moves.
    """
    registry = seeded(tmp_path / "registry")
    session = _closed(registry, "run-aged", OWES_NO)
    registry.onevcs("preserve", session.branch, "--repo", str(registry.checkout))
    origin = Path(registry.identity + ".git")
    assert git("branch", "--list", session.branch, cwd=origin).strip(), "not on its origin"

    aged = time.time() - 5 * 3600
    for top in ("sessions", "workspaces", "streams"):
        for directory, children, files in os.walk(registry.home / top):
            for name in (*children, *files):
                os.utime(Path(directory) / name, (aged, aged), follow_symlinks=False)
    record = registry.home / "sessions" / f"{session.token}.json"
    assert time.time() - record.stat().st_mtime > 4 * 3600

    runs = tmp_path / "runs"
    runs.mkdir()
    state = tmp_path / "state"
    scratch = tmp_path / "agentgraph"
    scratch.mkdir()
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    swept = _recipe(
        registry,
        runs,
        "sweep",
        state=state,
        ONEAGENTGRAPH_STATE_DIR=str(scratch),
        TMPDIR=str(temporary),
    )
    assert swept.status == 0, swept
    assert record.is_file(), f"the sweep removed the record of preserved unlanded work:\n{swept}"
    assert json.loads(record.read_text(encoding="utf-8"))["labels"]["launcher"] == OWES_NO

    listed = _recipe(
        registry, runs, "unpublished", "--session", OWES_NO, "--format", "json", state=state
    )
    assert listed.status == UNPUBLISHED, listed
    [row] = listed.document()["rows"]
    tip = git("rev-parse", session.branch, cwd=registry.checkout).strip()
    assert (row["branch"], row["tip"], row["counted"]) == (session.branch, tip, True)
    assert session.branch in _refused(_hook(registry, runs, state, OWES_NO))

    _acknowledge(registry, runs, state, OWES_NO, session.branch, "kept on its origin")
    _let_end(_hook(registry, runs, state, OWES_NO))

    commit_on(registry.checkout, session.branch, "moved.txt")
    assert session.branch in _refused(_hook(registry, runs, state, OWES_NO))
    moved = _recipe(registry, runs, "unpublished", "--session", OWES_NO, state=state)
    assert moved.status == UNPUBLISHED, moved


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


def test_the_own_sessions_read_lists_only_its_own_branches_at_this_hosts_scale(
    tmp_path: Path,
) -> None:
    """The read over the scale registry, timed as budget `own-sessions-read-seconds`."""
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
        ["just", "unpublished", "--session", SCALE_MANAGER, "--format", "json"],
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
    assert sorted(row["branch"] for row in json.loads(done.stdout)["rows"]) == sorted(own)
    budget_telemetry.record(
        OWN_READ_BUDGET,
        f"{IDENTITIES} identities, {IDENTITIES * CLOSED_PER_IDENTITY} closed sessions, "
        f"{PRESERVED} preserved branches",
        elapsed,
        unit="seconds",
        combine="max",
    )


def _scratch_checkout(root: Path, helper: str | None = None, *, engine: bool = True) -> Path:
    """A checkout holding the real justfile, the wrapper, the helper, the modules
    `unfinished.py` loads, the drafter graph and — unless ``engine`` is false — the
    installed engine where `bun.lock` puts it, and no `.venv`.

    With no `.venv/bin` beside the module, the interpreter is the `python3` on the search
    path; the engine is only ever the checkout's own, which is the one place a journey can
    hand it an engine that answers wrong. ``helper`` replaces the identity helper's text,
    and the empty string leaves it out.
    """
    if engine:
        locked = npm_installs.platform_binary(node_modules=root / "node_modules")
        locked.parent.mkdir(parents=True)
        locked.symlink_to(ONEPIPELINE)
    (root / "scripts").mkdir(parents=True)
    (root / "orchestrator").mkdir()
    (root / "graphs").mkdir()
    shutil.copy2(REPO_ROOT / "justfile", root / "justfile")
    shutil.copy2(REPO_ROOT / "scripts" / "unfinished.sh", root / "scripts" / "unfinished.sh")
    shutil.copy2(DRAFTER, root / "graphs" / DRAFTER.name)
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
    # `unfinished.py` reads the spike-brief receipts through `spike_brief.py`, which reads
    # run state through `run_reading.py`, and both resolve the engine through
    # `npm_installs.py`; each loads under the system interpreter as well.
    for module in (
        "unfinished.py",
        "spike_brief.py",
        "run_reading.py",
        "npm_installs.py",
        "root.py",
    ):
        shutil.copy2(REPO_ROOT / "orchestrator" / module, root / "orchestrator" / module)
    return root / "scripts" / "unfinished.sh"


#: What the decoy `onepipeline` on every wrapper journey's search path says when reached.
DECOY = "decoy onepipeline ahead of the locked one"


def _search_path(root: Path, *, python: bool = True) -> Path:
    """A search path of exactly the tools a run of the wrapper needs, led by a decoy engine.

    The installed `onevcs`, a decoy `onepipeline` that refuses every verb — so a journey
    answering at all proves the view asked the checkout's locked engine instead — `python3`
    unless ``python`` is false, and what the wrapper and `onevcs` spawn.
    """
    tools = root / "bin"
    tools.mkdir(parents=True)
    (tools / "onevcs").symlink_to(ONEVCS)
    decoy = tools / "onepipeline"
    decoy.write_text(f"#!/bin/sh\necho '{DECOY}' >&2\nexit 97\n", encoding="utf-8")
    decoy.chmod(0o755)
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
    environment = _environment(world.registry, world.runs, tmp_path / "state", None)
    environment["PATH"] = str(path)
    return subprocess.run(  # noqa: S603 - the tracked wrapper, copied whole
        [shutil.which("bash") or "/bin/bash", str(script), "--session", OWES_NO],
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
# The stand-in answers the unwatched half alone and hands `unpublished` to the installed
# engine, so the wrapper, the interpreter, the module, `onevcs` and the registry are real.
@pytest.mark.parametrize(
    ("engine", "said"),
    [
        pytest.param("echo 'engine noise' >&2; exit 3", "a status outside its vocabulary", id="3"),
        pytest.param("exit 6", "naming no run", id="6-naming-nothing"),
        pytest.param("exec sleep 120", "ran past its 60s bound", id="past-its-bound"),
    ],
)
def test_an_engine_that_cannot_answer_is_never_nothing_owed(
    world: World, tmp_path: Path, engine: str, said: str
) -> None:
    """Through the real wrapper: the unwatched half fails, the status says so, and the
    unpublished half that did answer is still shown."""
    script = _scratch_checkout(tmp_path / "checkout", engine=False)
    tools = _search_path(tmp_path)
    stand_in = npm_installs.platform_binary(node_modules=tmp_path / "checkout" / "node_modules")
    stand_in.parent.mkdir(parents=True)
    stand_in.write_text(
        f'#!/bin/sh\nif [ "$1" = unwatched ]; then {engine}; fi\nexec {ONEPIPELINE} "$@"\n',
        encoding="utf-8",
    )
    stand_in.chmod(0o755)
    done = _wrapper(world, tmp_path, script, tools)
    assert done.returncode == UNANSWERED, f"{done.stdout}\n{done.stderr}"
    assert "the unwatched half could not answer" in done.stderr and said in done.stderr
    assert "== unwatched:" not in done.stdout
    assert world.unlanded.branch in done.stdout, "the half that answered is still shown"


# llmlint: ignore-end[e2e_not_mocked]


def test_a_missing_engine_leaves_both_halves_unanswered(world: World, tmp_path: Path) -> None:
    """The decoy leading the search path is never asked in the locked engine's place."""
    script = _scratch_checkout(tmp_path / "checkout", engine=False)
    done = _wrapper(world, tmp_path, script, _search_path(tmp_path))
    assert done.returncode == UNANSWERED, done.stderr
    assert done.stderr.count("found no `onepipeline`") == 2 and done.stdout == ""
    assert DECOY not in done.stderr


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
    assert world.unlanded.branch in found.stdout and DECOY not in found.stderr

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


#: A module of this suite's own shape, planted where the session below collects it: a
#: module-scoped fixture lands a branch through the helper `world` above lands its own
#: with, before any test's own fixtures have run.
PLANTED_LANDING = """
import pytest
from unpublished_registry import seeded


@pytest.fixture(scope="module")
def landed(tmp_path_factory):
    registry = seeded(tmp_path_factory.mktemp("scrubbed") / "registry")
    session = registry.open_session(labels={"run": "run-scrubbed", "node": "node-1"})
    registry.close_session(session)
    return registry.run(
        "just", "publish-branch", session.branch, "--repo", str(registry.checkout), "--no-draft"
    )


def test_landed(landed):
    assert landed.returncode == 0, landed.stderr + landed.stdout
"""


def test_a_landing_registers_nothing_in_a_runs_root_the_suite_inherits(tmp_path: Path) -> None:
    """A suite run from inside a dispatch inherits that dispatch's runs root and its
    manager's session, and a landing a module-scoped fixture runs through the real
    `publish-branch` recipe registered a flow there under that manager — which its Stop
    hook then owed. The suite's ownership scrub reaches those fixtures' processes too."""
    runs = tmp_path / "runs"
    runs.mkdir()
    planted = tmp_path / "planted" / "test_planted_landing.py"
    planted.parent.mkdir()
    planted.write_text(PLANTED_LANDING, encoding="utf-8")
    environment = {
        **os.environ,
        "ONEPIPELINE_RUNS_DIR": str(runs),
        "ONEPIPELINE_LAUNCHER_SESSION": "the-inherited-manager",
        # Where this suite's own modules resolve from, so the session loads its conftest
        # for a module planted outside the tree.
        "PYTHONPATH": os.pathsep.join(str(REPO_ROOT / part) for part in ("tests", "tests/e2e")),
    }

    session = subprocess.run(
        [
            str(REPO_ROOT / ".venv" / "bin" / "python"),
            "-m",
            "pytest",
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:xdist",
            "--no-cov",
            "-q",
            f"--rootdir={REPO_ROOT}",
            str(planted),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )

    assert session.returncode == 0, session.stdout + session.stderr
    assert sorted(path.relative_to(runs) for path in runs.rglob("*")) == []
