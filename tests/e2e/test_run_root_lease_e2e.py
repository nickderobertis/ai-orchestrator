"""A live dispatch's run root survives a sibling `onevcs session open`.

Through onevcs 0.14.0 it did not. Every `session open` reclaimed the run roots of its
identity that nothing held an occupancy lease on, and `open` dropped the shared lease
it took the moment it returned — so from then on nothing held one while a dispatch
worked in that directory for hours, and a sibling dispatch opening its own session
deleted it. That destroyed three dispatches of one run on this host on 2026-08-22,
and reported each as a missing `claude` binary; the mechanism and the evidence are
`docs/run-root-reclamation.md`.

**onevcs 0.14.1 fixed it upstream** — `fix: prove a run root is abandoned from its
session record, not from a lease nothing holds`
(https://github.com/nickderobertis/onevcs/pull/82) — and this host adopted it through
`config/onevcs.version` 0.15.4. Measured on both binaries with everything else held,
a session whose record names a live owner now keeps its run root across a sibling
open, where 0.14.0 removed it.

**onevcs 0.15.6 then moved what "abandoned" means, deliberately, and journey (3) is
written to the release this host runs rather than to the one before it.**
`fix(workspace): keep a run root a live session is still working in`
(https://github.com/nickderobertis/onevcs/pull/99) protects a run root named by an
**open** session "even after the CLI process that opened the session exits", because a
sibling open was still deleting an active dispatch's worktree moments after launch —
the same ninety-second incident above, reached through the one door 0.14.1 left open.
Bisected here on 2026-08-29 against the released CLIs with everything else held: an
open session whose opener has exited keeps its run root from 0.15.6 onward and lost it
at 0.15.4 and 0.15.5, and every release through 0.16.0 behaves as 0.15.6 does. So owner
liveness is no longer what prunes, and journey (3) asks the question that now decides
it — a session that has been **closed** — which both 0.15.4 and the pinned release
answer the same way. What that trade costs is real and is not hidden: a session that
opens and is never closed keeps its run root indefinitely, where before its opener's
death was enough, and `just sweep` — which removes only what it can prove no live
process names — is what reclaims those.

`scripts/hold-run-lease.sh` is what this repository did about it from below while that
was outstanding: take that same shared lease and keep holding it for as long as the
session is live. It is kept and still driven here — it is no longer the only thing
standing between a dispatch and its directory, but a mitigation that has stopped being
load-bearing is not the same as one that has stopped working, and this suite is what
would say if it broke. These journeys drive it against the **real** `onevcs`, whose
`reclaim` is the thing under test — a double would be a restatement of the belief
being checked.

The first four are one argument and are worth reading in order:

1. the fix, reproduced — a live session's run root survives a sibling open with no
   lease held at all, which is what makes the rest of this a mitigation rather than
   a necessity;
2. the mitigation — the same sequence with the lease held also leaves the root alone;
3. the hygiene neither may cost — a **closed** session's root is pruned by the very
   next open, which is where that hygiene now comes from;
4. the wiring — a real `session-setup.sh`, which is what a dispatch runs at startup,
   really takes the lease.

The rest are the declines, and they matter for the same reason as (3): a hold that
could not tell a live session from a finished one, or that failed a session start
when a tool it wanted was not provisioned yet, would cost more than the unheld root
it was protecting against.

The identity is a scratch one against a scratch `ONEVCS_HOME`, because a test may not
register or open sessions against this host's own checkouts. The one thing arranged
rather than observed is the session's *owner*: the CLI that opens a session exits
immediately, so its record is stale on arrival, while a dispatched session is owned by
the live `onepipeline` driver. Each journey therefore points the record's owner at a
process it controls — the same record field, the same liveness rule `onevcs` itself
applies — and killing that process is how the release path is driven.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import NamedTuple, NewType

from provisioning import path_without_uv, run_setup, setup_repo
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The alias every session here is opened against.
EXECUTION_ALIAS = "execution"

#: A local-publishing scratch identity: nothing here reaches a remote.
RULES = """version: 3
trailer_prefix: Orchestrator-
rules:
  - match: {path: "*"}
    publication: local-direct
    approvals: none
default:
  publication: local-direct
  approvals: none
"""

#: The committer the seed commit carries. `tests/conftest.py` exports one per test,
#: but the seed is built before any of these journeys names a session.
GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")

#: What `session-setup.sh` says when it has the lease. Quoted rather than derived, so
#: a rewording of the line an operator reads fails here instead of passing silently.
HOLDING = "hold-run-lease: holding the occupancy lease on"


#: A session's own identifier. Distinguished from a plain string because it names a
#: record on disk and a run root beside it, and every journey below asks `onevcs`
#: about the *same* session it opened — which a bare `str` would let any other
#: identifier stand in for.
SessionToken = NewType("SessionToken", str)


class Identity(NamedTuple):
    """One scratch identity, and the environment that reaches it."""

    #: The registry, the locks, the session records, and the run roots.
    home: Path
    #: The registered execution checkout every session is cut from.
    checkout: Path
    #: `ONEVCS_HOME` pointed at `home`, for every real `onevcs` call below.
    environment: dict[str, str]


class Session(NamedTuple):
    """One really-opened session, and the process standing in for its driver."""

    token: SessionToken
    worktree: Path
    run_root: Path
    #: The lock file `reclaim` takes exclusively to judge this root dead.
    lock: Path
    #: The live process the record names as the session's owner.
    owner: subprocess.Popen[bytes]


def _git(*arguments: str, cwd: Path) -> str:
    """Run git for real, failing loudly."""
    done = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"git {' '.join(arguments)}: {done.stderr or done.stdout}"
    return done.stdout


def _identity(tmp_path: Path) -> Identity:
    """A registered scratch identity whose state root is where `$HOME` says it is.

    `.onevcs` under `tmp_path` deliberately: `run_setup` below redirects `$HOME` there,
    and `onevcs` resolves its state root from `$HOME` when `ONEVCS_HOME` is unset, so
    the session-setup journey reaches this same registry without the fixture having to
    thread an extra variable into a script it is proving unmodified.
    """
    home = tmp_path / ".onevcs"
    home.mkdir()
    seed = tmp_path / "seed"
    _git("init", "-q", "-b", "main", str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    origin = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)
    checkout = tmp_path / EXECUTION_ALIAS
    _git("clone", "-q", str(origin), str(checkout), cwd=tmp_path)
    manifest = tmp_path / "onevcs.checkouts"
    manifest.write_text(f"{checkout}\n", encoding="utf-8")
    rules = tmp_path / "onevcs.rules.yml"
    rules.write_text(RULES, encoding="utf-8")
    environment = {**os.environ, "ONEVCS_HOME": str(home)}
    applied = subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), "--rules", str(rules)],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert applied.returncode == 0, f"repos-apply failed:\n{applied.stdout}\n{applied.stderr}"
    return Identity(home=home, checkout=checkout, environment=environment)


def _process_started(pid: int) -> int:
    """The creation identity Linux gives a pid, as `onevcs` records it.

    Field 22 of `/proc/<pid>/stat`, counted from the last `)` because the command name
    is parenthesized. Restated here rather than imported because the producer is a Rust
    crate; `scripts/hold-run-lease.sh` reads the same field, and a journey that took its
    answer from the script could not tell a wrong field from a matching mistake.
    """
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return int(stat.rsplit(")", 1)[1].split()[19])


def _open_session(identity: Identity) -> Session:
    """Open one real session and give its record a live owner.

    The owner is a process this test can kill, because that is the difference between a
    session a dispatch holds and the stale record a bare CLI invocation leaves — and the
    whole question here is which of the two the reclaimer may delete.
    """
    opened = subprocess.run(
        ["uv", "run", "onevcs", "session", "open", EXECUTION_ALIAS],
        cwd=REPO_ROOT,
        env=identity.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert opened.returncode == 0, f"session open failed:\n{opened.stdout}\n{opened.stderr}"
    reported = json.loads(opened.stdout.strip())
    token = SessionToken(reported["token"])
    worktree = Path(reported["worktree"])
    run_root = worktree.parent
    owner = subprocess.Popen(["sleep", str(int(e2e_timeout(600)))])
    record_path = identity.home / "sessions" / f"{token}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    # No command line leaves a session live. `onevcs session open` records the pid of a
    # process that has already exited by the time it prints, so every CLI-opened session
    # is stale on arrival; a live one exists only while an embedder holds it —
    # `onepipeline`, whose driver pid a dispatched session records — and this suite may
    # not run that, because it would launch paid agents. So the two fields that say "a
    # driver is holding this" are pointed at a process the journey controls: the same
    # record `onevcs` writes, the same fields `Record::liveness` reads, and the only way
    # to drive the release path at all, since releasing is what happens when it dies.
    # llmlint: ignore[tests_mirror_real_usage] No interface leaves a session live; see above.
    record_path.write_text(
        json.dumps(
            {**record, "owner_pid": owner.pid, "owner_started": _process_started(owner.pid)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return Session(
        token=token,
        worktree=worktree,
        run_root=run_root,
        lock=_lock_path(identity.home, run_root),
        owner=owner,
    )


def _lock_path(home: Path, run_root: Path) -> Path:
    """The lock file `onevcs` guards one run root's occupancy with.

    `lock::path_for` hashes the lease identity, and `workspace::occupancy_identity`
    spells a run root's identity `run:<path>`. Restated for the same reason the
    `/proc` field above is: the producer is another crate, and this is the file whose
    contention decides whether a directory is deleted.
    """
    digest = hashlib.sha256(f"run:{run_root}".encode()).hexdigest()
    return home / "locks" / f"{digest}.lock"


#: What `flock` is told to exit with when the lease is contended, so that contention
#: is distinguishable from `flock` failing — which shares exit 1 with everything else.
CONFLICT_EXIT = 9


def _lease_is_held(lock: Path) -> bool:
    """Whether anything holds the lease — the exact question `reclaim` asks of it.

    Only the conflict code counts as held. A journey that read every non-zero exit as
    contention would call a lease held when `flock` could not open the file at all,
    which is the state one of the journeys below deliberately builds.
    """
    if not lock.exists():
        return False
    taken = subprocess.run(
        [
            "flock",
            "--exclusive",
            "--nonblock",
            "--conflict-exit-code",
            str(CONFLICT_EXIT),
            str(lock),
            "true",
        ],
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert taken.returncode in (0, CONFLICT_EXIT), (
        f"flock could not answer for {lock}: {taken.returncode} {taken.stderr!r}"
    )
    return taken.returncode == CONFLICT_EXIT


def _hold(
    worktree: Path,
    identity: Identity,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real script the way session setup runs it, from the dispatch's worktree."""
    return subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "hold-run-lease.sh"), str(worktree)],
        env=identity.environment if environment is None else environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _path_without(tool: str, tmp_path: Path) -> str:
    """A real PATH with one tool genuinely absent — nothing here is a double.

    Every other executable is the host's own, linked from `/usr/bin` and `/bin`; what
    the narrowing removes is the one binary whose absence is under test, the same way
    `provisioning.path_without_uv` removes `uv`. Substituting a failing stub instead
    would prove the script handles a stub.
    """
    tools = tmp_path / f"without-{tool}"
    tools.mkdir(exist_ok=True)
    for directory in (Path("/usr/bin"), Path("/bin")):
        if not directory.is_dir():
            continue
        for executable in directory.iterdir():
            if executable.name == tool or (tools / executable.name).exists():
                continue
            (tools / executable.name).symlink_to(executable)
    return str(tools)


def _release(session: Session) -> None:
    """End the session's life and wait for the holder to let go, leaving nothing behind."""
    session.owner.kill()
    session.owner.wait(timeout=e2e_timeout(60))
    until = deadline(120)
    while _lease_is_held(session.lock):
        assert time.monotonic() < until, f"the lease on {session.run_root} was never released"
        time.sleep(1)


def _sibling_opens_a_session(identity: Identity) -> None:
    """What every dispatch of the same identity does at startup, and what reclaims."""
    opened = subprocess.run(
        ["uv", "run", "onevcs", "session", "open", EXECUTION_ALIAS],
        cwd=REPO_ROOT,
        env=identity.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert opened.returncode == 0, f"the sibling open failed:\n{opened.stdout}\n{opened.stderr}"


def test_a_sibling_session_open_spares_an_unheld_live_dispatchs_run_root(
    tmp_path: Path,
) -> None:
    """The fix itself, against the real binary, with no lease held at all.

    This leg used to assert the opposite, and flipping it is the whole news of the
    onevcs 0.14.1 adoption: `reclaim` proves abandonment from the session record it
    already has rather than from a lease nobody holds, so the record saying *open* with
    a live owner is now enough to spare the directory. Driven with no lease deliberately
    — the mitigation below holds one, and a leg that held one here could not tell the
    fix from the workaround.

    The record is read afterwards as well as the directory, because the two agreeing is
    what makes the answer sound: the failure this replaces was `onevcs` calling a
    session open and live while the worktree it named was gone, and a run root spared
    beside a record that had quietly closed would be the same disagreement inverted.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        assert session.worktree.is_dir()
        assert not _lease_is_held(session.lock), (
            "this leg is the unmitigated one and something is already holding the lease "
            "on its run root, so it would pass whether or not onevcs spares a live root"
        )

        _sibling_opens_a_session(identity)

        assert session.run_root.exists(), (
            f"{session.run_root} was reclaimed while its session record named a live "
            "owner. That is the 2026-08-22 defect back: the adopted onevcs is proving "
            "abandonment from a lease nothing holds rather than from the record"
        )
        assert session.worktree.is_dir(), (
            f"{session.run_root} survived but its worktree did not, so a dispatch "
            "working there still loses the directory its child processes are running in"
        )
        record = json.loads(
            (identity.home / "sessions" / f"{session.token}.json").read_text(encoding="utf-8")
        )
        assert record["state"] == "open"
    finally:
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_holding_the_lease_keeps_a_live_dispatchs_run_root(tmp_path: Path) -> None:
    """The mitigation: the same sequence, with the lease held.

    Held for real, not reported as held — the assertion is that the directory and its
    worktree are still there after a sibling has opened its own session, which is the
    only thing the destroyed dispatches needed.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        held = _hold(session.worktree, identity)
        assert held.returncode == 0, held.stderr
        assert f"{HOLDING} {session.run_root}" in held.stderr, held.stderr
        assert _lease_is_held(session.lock), held.stderr

        _sibling_opens_a_session(identity)

        assert session.run_root.is_dir(), f"{session.run_root} was reclaimed while held"
        assert session.worktree.is_dir()
    finally:
        _release(session)


def test_an_open_sessions_run_root_survives_its_opener_exiting(tmp_path: Path) -> None:
    """The protection onevcs 0.15.6 added, with no lease held and no owner alive.

    This is the door 0.14.1 left open: that release proved a root abandoned from a
    *live owner* in its session record, so a dispatch whose opening CLI had returned —
    which is every dispatch, moments after launch — looked abandoned to the next
    sibling open. Asserted here because this repository's prose now claims it and
    because it is the half that decides whether a live dispatch keeps its worktree; the
    journey below asserts the hygiene it must not have cost.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))
        assert not _lease_is_held(session.lock), (
            "no lease is held here on purpose: the protection under test is the session "
            "record's, and a held lease would prove the mitigation instead"
        )

        _sibling_opens_a_session(identity)

        assert session.run_root.is_dir(), (
            f"{session.run_root} was reclaimed after its opener exited; an open session's "
            "root is protected from onevcs 0.15.6 onward, and losing it is what destroyed "
            "three dispatches within ninety seconds"
        )
        assert session.worktree.is_dir()
    finally:
        session.owner.kill()


def test_a_closed_sessions_run_root_is_reclaimed_by_the_next_open(
    tmp_path: Path,
) -> None:
    """The hygiene the mitigation must not cost, asked the way the pinned release decides it.

    The bounded recovery history exists because unpruned scratch fills this host's
    disk, so a hold that outlived its dispatch would trade one failure for a worse one.
    Since onevcs 0.15.6 an *open* session's root is protected even once its opener has
    exited — see the module docstring — so the owner dying is no longer the question.
    Closing the session is, so the sequence is: hold the lease, end the owner, release
    the hold, close the session, and require the very next sibling open to reclaim the
    root. The hold is taken and released rather than skipped because what must be shown
    is that the *mitigation* leaves nothing behind, not merely that close prunes.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    held = _hold(session.worktree, identity)
    assert held.returncode == 0, held.stderr
    assert _lease_is_held(session.lock), held.stderr

    _release(session)
    closed = subprocess.run(
        ["uv", "run", "onevcs", "session", "close", session.token],
        cwd=REPO_ROOT,
        env=identity.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(180),
        check=False,
    )
    assert closed.returncode == 0, f"{closed.stdout}\n{closed.stderr}"

    _sibling_opens_a_session(identity)
    assert not session.run_root.exists(), (
        f"{session.run_root} outlived its closed session, so this hold is now the thing "
        "keeping dead scratch on disk"
    )


def test_the_holder_declines_a_session_whose_owner_is_already_gone(tmp_path: Path) -> None:
    """A stale session is left reclaimable, and says why.

    This is the same guarantee as the journey above at the other end: the script is run
    against a session whose owner never survives it, and it must take nothing. A hold
    that could not tell the two apart would protect every abandoned run root on the host.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    session.owner.kill()
    session.owner.wait(timeout=e2e_timeout(60))

    declined = _hold(session.worktree, identity)

    assert declined.returncode == 0, declined.stderr
    assert f"nothing to hold: session {session.token} is open but the process" in declined.stderr
    assert not _lease_is_held(session.lock), declined.stderr


def test_the_holder_declines_a_directory_that_is_no_dispatch_worktree(tmp_path: Path) -> None:
    """An ordinary checkout is not a run root, and `just bootstrap` runs there too.

    A live session exists on the host while this runs, which is the case that matters:
    the script has to decline because *this* directory is nobody's worktree, not
    because it found no sessions — and it must not reach for the lease of the one it
    did find.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        declined = _hold(identity.checkout, identity)

        assert declined.returncode == 0, declined.stderr
        assert "is not the worktree of any recorded session" in declined.stderr
        assert not _lease_is_held(session.lock), declined.stderr
    finally:
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_session_start_provisioning_takes_the_lease_before_it_provisions(
    tmp_path: Path,
) -> None:
    """The wiring, driven end to end: a dispatch's own session start really holds it.

    `session-setup.sh` is what a dispatched worktree runs at startup, so this runs the
    real script inside a real session worktree and reads the real lock afterwards.
    `uv` is genuinely absent — `path_without_uv` removes it rather than doubling it —
    so the provisioning that follows fails fast and the run stays cheap; the lease is
    taken before any of that, which is the ordering the whole mitigation depends on and
    the reason this journey can afford to prove it.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        setup_repo(tmp_path, repo=session.worktree)

        provisioned = run_setup(session.worktree, tmp_path, path=path_without_uv(tmp_path))

        assert f"{HOLDING} {session.run_root}" in provisioned.stderr, provisioned.stderr
        assert _lease_is_held(session.lock), provisioned.stderr
        # The toolchain is genuinely unavailable on this PATH, and setup says so by
        # exiting non-zero. That is this journey's cost of being fast, not its subject.
        assert "cannot install required project dependencies" in provisioned.stderr
    finally:
        _release(session)


def test_the_holder_declines_a_session_whose_record_says_it_closed(tmp_path: Path) -> None:
    """A closed session is finished, and its run root is the reclaimer's to take.

    The owner is still alive here, so the record's own state is the only thing saying
    the work is over — which is exactly the case a hold keyed on liveness alone would
    get wrong, and it is the state every settled node leaves behind.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        record_path = identity.home / "sessions" / f"{session.token}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        # `onevcs session close` would close this session *and* hand its branch back and
        # tear its worktree down — removing the very directory whose protection is the
        # subject. What is under test is a record that says closed while its owner is
        # still alive, which is what a settled node leaves and what no verb produces on
        # demand.
        # llmlint: ignore[tests_mirror_real_usage] `close` would delete the subject; see above.
        record_path.write_text(
            json.dumps({**record, "state": "closed"}, indent=2) + "\n", encoding="utf-8"
        )

        declined = _hold(session.worktree, identity)

        assert declined.returncode == 0, declined.stderr
        assert f"session {session.token} is 'closed' rather than open" in declined.stderr
        assert not _lease_is_held(session.lock), declined.stderr
    finally:
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_the_holder_declines_and_says_so_when_a_tool_it_needs_is_missing(
    tmp_path: Path,
) -> None:
    """Neither reader nor lease is optional, and a session start must survive both.

    A dispatched worktree is provisioned *after* this runs, so "the tool is not there
    yet" is a real state rather than a hypothetical one. Each tool is genuinely
    removed from PATH, and each decline has to name the tool and leave the lease alone
    — a script that took a lease it could not hold, or that failed the hook, would
    cost the dispatch more than the unheld root does.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        for tool, expected in (
            ("python3", "no python3 on PATH"),
            ("flock", "no flock on PATH"),
        ):
            declined = _hold(
                session.worktree,
                identity,
                environment={**identity.environment, "PATH": _path_without(tool, tmp_path)},
            )

            assert declined.returncode == 0, declined.stderr
            assert expected in declined.stderr, declined.stderr
            assert not _lease_is_held(session.lock), declined.stderr
    finally:
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_a_launch_that_never_reached_the_holder_is_reported_with_a_next_action(
    tmp_path: Path,
) -> None:
    """What bash writes to the holder's stream is not a diagnosis, and is not relayed as one.

    `setsid` is what detaches the holder, and it is not one of the tools the entry
    point checks for — a PATH without it fails inside the launch itself, so the only
    thing on the holder's stream is bash's own `command not found`. Relayed verbatim
    that names what broke and nothing to do about it, and it reads exactly like the
    holder's own declines, which all carry their repair.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        unlaunchable = _hold(
            session.worktree,
            identity,
            environment={**identity.environment, "PATH": _path_without("setsid", tmp_path)},
        )

        assert unlaunchable.returncode == 0, unlaunchable.stderr
        assert "could not confirm the occupancy lease" in unlaunchable.stderr, unlaunchable.stderr
        assert "the holder never reached its own diagnostics" in unlaunchable.stderr, (
            unlaunchable.stderr
        )
        assert "setsid" in unlaunchable.stderr, unlaunchable.stderr
        assert not _lease_is_held(session.lock), unlaunchable.stderr
    finally:
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_the_holder_declines_when_no_state_root_can_be_named(tmp_path: Path) -> None:
    """With neither `ONEVCS_HOME` nor `HOME`, there is no registry to read at all."""
    identity = _identity(tmp_path)
    stripped = {
        key: value
        for key, value in identity.environment.items()
        if key not in {"ONEVCS_HOME", "HOME"}
    }

    declined = _hold(identity.checkout, identity, environment=stripped)

    assert declined.returncode == 0, declined.stderr
    assert "no onevcs state root" in declined.stderr, declined.stderr

    # A root that is two lines is not a directory this script may act on: the lock path
    # it derives travels back through a line-oriented protocol.
    broken = _hold(
        identity.checkout,
        identity,
        environment={**identity.environment, "ONEVCS_HOME": f"{identity.home}\nsecond"},
    )

    assert broken.returncode == 0, broken.stderr
    assert "state root carries a line break" in broken.stderr, broken.stderr


def _rewrite_record(
    identity: Identity,
    session_token: SessionToken,
    fields: dict[str, object],
    pristine: Path,
) -> Path:
    """Put one session record into a shape `onevcs` would not have written.

    `pristine` is the record as it stood before any of this, because these shapes are
    driven one after another and a run root broken by the previous case would decide
    the next one.
    """
    # The record is the boundary under test: the script reads it before any `onevcs` is
    # on a fresh worktree's PATH, so what it has to survive is a record `onevcs` did not
    # write — a token that is no token, a relative run root, an owner that is not a
    # process. No interface produces those, which is why nothing but writing them proves
    # the validation runs at all.
    path = identity.home / "sessions" / f"{session_token}.json"
    record = json.loads(pristine.read_text(encoding="utf-8"))
    # llmlint: ignore[tests_mirror_real_usage] No interface writes a broken record; see above.
    path.write_text(json.dumps({**record, **fields}, indent=2) + "\n", encoding="utf-8")
    return path


def test_the_holder_declines_a_record_it_cannot_use(tmp_path: Path) -> None:
    """Every field the script validates, rejected in the shape that would matter.

    A run root is a path this script hands to `flock`, and a token is a file name it
    reads, so a record carrying something else is the one input that could make it act
    on a directory nobody named. Each rejection has to leave the lease alone: a
    validation that declined and then took the lease anyway would be worse than none.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    pristine = tmp_path / "pristine-record.json"
    pristine.write_text(
        (identity.home / "sessions" / f"{session.token}.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    try:
        for fields, expected in (
            ({"token": "../elsewhere"}, "names no usable session token"),
            ({"run_root": "relative/run"}, "names no usable run root"),
            ({"run_root": f"{session.run_root}\ninjected"}, "names no usable run root"),
            ({"owner_pid": 0}, "names no owning process"),
            ({"owner_started": None}, "records no creation identity"),
        ):
            _rewrite_record(identity, session.token, fields, pristine)

            declined = _hold(session.worktree, identity)

            assert declined.returncode == 0, declined.stderr
            assert expected in declined.stderr, declined.stderr
            assert not _lease_is_held(session.lock), declined.stderr
    finally:
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_the_holder_declines_when_no_session_record_can_be_read(tmp_path: Path) -> None:
    """A state root whose `sessions` is not a directory answers nothing, and says so."""
    identity = _identity(tmp_path)
    sessions = identity.home / "sessions"
    assert not sessions.exists(), "this identity has opened no session, so nothing wrote one"
    sessions.write_text("not a directory\n", encoding="utf-8")

    declined = _hold(identity.checkout, identity)

    assert declined.returncode == 0, declined.stderr
    assert f"no session records could be read from {sessions}" in declined.stderr


def test_a_holder_that_cannot_take_the_lease_says_why_rather_than_timing_out(
    tmp_path: Path,
) -> None:
    """The confirmation is a real check, and its failure names a cause.

    A lease this script reports as held but does not hold would be the worst outcome
    available to it — the dispatch would be exposed and nothing would say so. The lock
    path is made unopenable, which is the one failure a caller can build, and the
    parent has to come back with the holder's own reason rather than with a timeout.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        # `onevcs` created this file when it took its own lease, so the way to make it
        # unopenable is to take the permissions off it rather than to put something
        # else in its place.
        session.lock.chmod(0o000)

        reported = _hold(session.worktree, identity)

        assert reported.returncode == 0, reported.stderr
        assert f"could not confirm the occupancy lease on {session.run_root}" in reported.stderr
        assert "could not be opened for writing" in reported.stderr, reported.stderr
    finally:
        session.lock.chmod(0o644)
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_the_internal_holder_entry_refuses_argv_it_cannot_trust(tmp_path: Path) -> None:
    """`--hold` re-enters this file by name, so what reaches it is argv.

    It is reached through `setsid`, which is a process boundary and not a function
    call: whatever a caller puts on that command line is what the holding process acts
    on, and it holds a lease and outlives its parent. It takes the same one directory
    the public entry takes and additionally requires it absolute, because the holder
    leaves that directory before it holds. Driving it is the only way to show that
    check is really performed.

    llmlint: ignore[tests_mirror_real_usage] `setsid` invokes this argv for real.
    """
    identity = _identity(tmp_path)
    script = str(REPO_ROOT / "scripts" / "hold-run-lease.sh")
    for arguments, expected in (
        ([], "takes exactly one absolute directory, not 0 arguments"),
        (
            [str(identity.checkout), str(identity.home)],
            "takes exactly one absolute directory, not 2 arguments",
        ),
        (["relative/worktree"], "takes an absolute directory, not 'relative/worktree'"),
    ):
        # llmlint: ignore[tests_mirror_real_usage] `setsid` invokes this argv for real.
        refused = subprocess.run(
            ["bash", script, "--hold", *arguments],
            env=identity.environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )

        assert refused.returncode == 1, refused.stderr
        assert expected in refused.stderr, refused.stderr


def test_the_holder_takes_at_most_one_directory(tmp_path: Path) -> None:
    """A second argument is a caller that means something this script does not do."""
    identity = _identity(tmp_path)

    refused = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "hold-run-lease.sh"),
            str(identity.checkout),
            str(identity.home),
        ],
        env=identity.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode == 0, refused.stderr
    assert "takes at most one directory, not 2 arguments" in refused.stderr


def test_an_unreadable_holder_log_is_reported_rather_than_read_as_silence(
    tmp_path: Path,
) -> None:
    """The diagnosis has to survive its own evidence being unavailable.

    The holder writes why it could not take the lease to a log beside the lock, and
    the parent quotes it. A parent that read an unreadable log as an empty one would
    report the confirmation failure with no cause at all — the same silence the whole
    change is about — so the log path is made unreadable and the report has to say so.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    holder_log = session.lock.with_suffix(".holder.log")
    try:
        # A directory where the log goes: the redirect that would create the holder
        # fails, and what the parent has to read back is unreadable rather than empty.
        holder_log.mkdir(parents=True)

        reported = _hold(session.worktree, identity)

        assert reported.returncode == 0, reported.stderr
        assert f"the holder log {holder_log} exists but could not be read" in reported.stderr
    finally:
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_a_record_that_cannot_be_parsed_is_skipped_rather_than_fatal(tmp_path: Path) -> None:
    """One unreadable record must not cost every other session its protection.

    This host keeps hundreds of session records and every one of them is read on the
    way to finding this dispatch's. A half-written or truncated one is a state a
    crashed writer really leaves, and a reader that stopped there would leave a live
    run root unheld because of somebody else's session.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        (identity.home / "sessions" / "s-000000000000.json").write_text(
            "{ not json at all", encoding="utf-8"
        )
        (identity.home / "sessions" / "s-000000000001.json").write_text(
            json.dumps(["a list, not a record"]), encoding="utf-8"
        )

        held = _hold(session.worktree, identity)

        assert held.returncode == 0, held.stderr
        assert f"{HOLDING} {session.run_root}" in held.stderr, held.stderr
        assert _lease_is_held(session.lock), held.stderr
    finally:
        _release(session)


def test_a_record_naming_an_unresolvable_worktree_is_skipped_rather_than_fatal(
    tmp_path: Path,
) -> None:
    """A record that parses is still not one this reader can act on.

    Matching a record to this dispatch means resolving the worktree it names, and an
    embedded NUL is the one value `os.path.realpath` refuses outright. Unhandled, that
    is a traceback out of the reader and a caller diagnosis about a sessions directory
    it cannot read — for a directory it read perfectly well — while the live session
    beside it goes unprotected because of somebody else's record.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        # llmlint: ignore[tests_mirror_real_usage] No interface writes a record naming a
        # path the OS refuses; writing one is the only way to reach this reader's boundary.
        (identity.home / "sessions" / "s-000000000002.json").write_text(
            json.dumps({"token": "s-000000000002", "worktree": "/a\u0000b"}),
            encoding="utf-8",
        )

        held = _hold(session.worktree, identity)

        assert held.returncode == 0, held.stderr
        assert f"{HOLDING} {session.run_root}" in held.stderr, held.stderr
        assert _lease_is_held(session.lock), held.stderr
    finally:
        _release(session)


def test_the_holder_defaults_to_the_directory_it_was_run_in(tmp_path: Path) -> None:
    """Session setup names the worktree, but a hand-run in one must work too.

    `just bootstrap` and an operator debugging a dispatch both run this from inside
    the worktree with no argument, and that has to reach the same session.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        held = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "hold-run-lease.sh")],
            cwd=session.worktree,
            env=identity.environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )

        assert held.returncode == 0, held.stderr
        assert f"{HOLDING} {session.run_root}" in held.stderr, held.stderr
        assert _lease_is_held(session.lock), held.stderr
    finally:
        _release(session)


def test_the_lease_is_released_when_the_session_is_handed_to_another_owner(
    tmp_path: Path,
) -> None:
    """A record rewritten for a different owner ends this hold.

    The engine reuses a run root: a stopped run's session is taken up again, and the
    record is then somebody else's. A holder that went on holding for the owner it
    started with would be protecting a session that no longer exists, under a lease
    the new owner's own holder could not tell apart from a live one.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    successor = subprocess.Popen(["sleep", str(int(e2e_timeout(600)))])
    try:
        held = _hold(session.worktree, identity)
        assert held.returncode == 0, held.stderr
        assert _lease_is_held(session.lock), held.stderr

        _rewrite_record(
            identity,
            session.token,
            {
                "owner_pid": successor.pid,
                "owner_started": _process_started(successor.pid),
            },
            identity.home / "sessions" / f"{session.token}.json",
        )

        until = deadline(120)
        while _lease_is_held(session.lock):
            assert time.monotonic() < until, "the hold outlived the owner it was taken for"
            time.sleep(1)
    finally:
        successor.kill()
        successor.wait(timeout=e2e_timeout(60))
        session.owner.kill()
        session.owner.wait(timeout=e2e_timeout(60))


def test_the_holder_resolves_a_relative_directory_before_it_holds(tmp_path: Path) -> None:
    """`hold-run-lease.sh .` is how it is typed by hand, and it has to work.

    The holding process leaves the directory it was started in — it may have to outlive
    it — so a relative path would name nothing by the time the lease is taken. Resolving
    it is therefore not a convenience: an unresolved one reaches the holder, is refused
    there, and the caller is told only that the lease could not be confirmed.
    """
    identity = _identity(tmp_path)
    session = _open_session(identity)
    try:
        held = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "hold-run-lease.sh"), session.worktree.name],
            cwd=session.run_root,
            env=identity.environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )

        assert held.returncode == 0, held.stderr
        assert f"{HOLDING} {session.run_root}" in held.stderr, held.stderr
        assert _lease_is_held(session.lock), held.stderr
    finally:
        _release(session)
