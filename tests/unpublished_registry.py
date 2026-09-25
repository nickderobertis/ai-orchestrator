"""A scratch `onevcs` registry holding every branch state `just unpublished` reads.

Both the unit tier over `orchestrator/unpublished.py` and the journey over the recipe
need the same thing: a registered identity, made from nothing, on which the real
`onevcs` has preserved a branch each way a run can leave one — a session that committed
and closed, a session still held by a live process, and a branch no session record names
— under a scratch `ONEVCS_HOME`, so nothing this host has registered is read or moved.
This module seeds it once, the way `tests/e2e/test_recoverable_resume_commands_e2e.py`
seeds its own, and every step is the real verb: `just register-repo`, `onevcs session
open`, a real commit in the session's worktree, `onevcs session close`.

**The one thing arranged rather than observed is a live session's owner**:
`onevcs session open` records the
pid of a process that has exited by the time it prints, so every CLI-opened session is
stale on arrival, and a live one exists only while an embedder — the `onepipeline`
driver — holds it, which this suite may not run. So :func:`hold` points the record's
owner at a process the test controls: the same record `onevcs` writes, the same two
fields `Record::liveness` reads, and `recoverable` then reports the branch `held_by` a
running owner exactly as it does for a dispatch.

llmlint: ignore-file[shell_test_tiers_stay_split] This module is infrastructure for both
tiers that read it, and which tier each of them is decides where it can live: the recipe
half is split out into `tests/e2e/unpublished_view/test_unpublished_e2e.py`, the
`unpublished-view` project keyed on `unpublishedViewWorkspace`, while
`tests/test_unpublished.py` must stay in `orchestrator:test` because
`orchestrator/project.json` runs `test-recipes` with `--no-cov` and gives `coverage` a
`dependsOn` of `test` alone — so that is the one tier whose measurement the 100% floor over
`orchestrator/` is read from. Moving this helper into that project's directory would not
change what either reads: both import it, so it sits beside the suite modules as the
`support-unpublished-registry` unit, and each project reaches it through its dependency on
that unit.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, NewType

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The base every branch here is preserved against.
BASE = "main"

#: A branch no session record names, which `recoverable` lists as host-level only.
ORPHAN_BRANCH = "claude/orphan-work"

#: The committer every commit here carries; `tests/conftest.py` exports one per test, and
#: a module-scoped seed runs before it has.
GIT_IDENTITY = ("-c", "user.email=test@example.com", "-c", "user.name=ai-orchestrator-test")

#: A workspaces file pooling nothing, so every session here is cut under `runs/` — the
#: placement whose run root a closed session keeps while its clone holds unpublished
#: work, which is the disk this view exists to show.
UNPOOLED = "version: 1\ndefault:\n  pool: 0\n  overflow: unlimited\nrules: []\n"

#: A local-publishing policy, so nothing here reaches a remote.
RULES = (
    "version: 3\n"
    "trailer_prefix: Orchestrator-\n"
    "rules: []\n"
    "default:\n"
    "  publication: local-direct\n"
    "  approvals: none\n"
)

SessionToken = NewType("SessionToken", str)


class Session(NamedTuple):
    """One real `onevcs` session: its token, the branch it holds, and where it works."""

    token: SessionToken
    branch: str
    worktree: Path

    @property
    def run_root(self) -> Path:
        return self.worktree.parent


class Registry(NamedTuple):
    """One scratch identity and the environment that reaches it."""

    #: The scratch `ONEVCS_HOME`.
    home: Path
    #: The registered checkout, which is also the publication checkout.
    checkout: Path
    #: The identity `onevcs` files the checkout under: its bare origin's path, as the
    #: registry spells a path origin — without the `.git`.
    identity: str
    #: The whole environment a process reaching this registry runs under.
    environment: dict[str, str]

    def run(self, *arguments: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        """Run one command from this checkout against the scratch registry."""
        return subprocess.run(
            arguments,
            cwd=REPO_ROOT,
            env={**self.environment, **overrides},
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )

    def onevcs(self, *arguments: str) -> str:
        """Run one `onevcs` verb for real, failing loudly, and answer its stdout."""
        done = self.run("uv", "run", "onevcs", *arguments)
        assert done.returncode == 0, f"onevcs {' '.join(arguments)}:\n{done.stdout}\n{done.stderr}"
        return done.stdout

    def open_session(self, *, branch: str | None = None) -> Session:
        """Open a real session, and commit one file on its branch so it is preserved."""
        arguments = ["session", "open", str(self.checkout)]
        if branch is not None:
            arguments += ["--branch", branch]
        reported = json.loads(self.onevcs(*arguments).strip())
        session = Session(
            token=SessionToken(reported["token"]),
            branch=reported["branch"],
            worktree=Path(reported["worktree"]),
        )
        commit(session.worktree, f"work-{session.token}.txt")
        return session

    def close_session(self, session: Session) -> None:
        self.onevcs("session", "close", session.token)

    def hold(self, session: Session) -> subprocess.Popen[bytes]:
        """Give ``session`` a live owner the caller kills, as the module docstring says.

        A held session is what `recoverable` reports `held_by` a running owner; killing
        the owner is what makes the branch a preserved one again.
        """
        owner = subprocess.Popen(["sleep", str(int(e2e_timeout(600)))])
        record_path = self.home / "sessions" / f"{session.token}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        # llmlint: ignore[tests_mirror_real_usage] No interface leaves a session live; see above.
        record_path.write_text(
            json.dumps(
                {**record, "owner_pid": owner.pid, "owner_started": process_started(owner.pid)},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return owner

    def hold_lease(self, session: Session) -> subprocess.Popen[bytes]:
        """Hold the real run-root lease while `onevcs` computes occupancy.

        Installed `onevcs 0.30.1` (`workspace::occupancy_identity`,
        `lock::path_for`, `ids::digest`) keys this advisory lock on
        `run:<run_root>` under `<ONEVCS_HOME>/locks`. The journey checks both
        sides of this lease, so a changed layout cannot silently pass.
        """
        identity = f"run:{session.run_root}"
        digest = hashlib.sha256(identity.encode()).hexdigest()
        lock_path = self.home / "locks" / f"{digest}.lock"
        assert lock_path.is_file(), f"onevcs did not create its run-root lease: {lock_path}"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import fcntl, sys; "
                    "lease = open(sys.argv[1], 'r+b'); "
                    "fcntl.flock(lease, fcntl.LOCK_SH); "
                    "sys.stdout.buffer.write(b'ready'); sys.stdout.buffer.flush(); "
                    "sys.stdin.buffer.read()"
                ),
                str(lock_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert child.stdout is not None
        ready, _, _ = select.select([child.stdout], [], [], e2e_timeout(30))
        assert ready, f"lease holder did not answer before the timeout: {lock_path}"
        assert child.stdout.read(5) == b"ready", f"lease holder could not acquire {lock_path}"
        return child


def git(*arguments: str, cwd: Path) -> str:
    """Run git for real, failing loudly."""
    done = subprocess.run(
        ["git", *GIT_IDENTITY, *arguments],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert done.returncode == 0, f"git {' '.join(arguments)}: {done.stderr or done.stdout}"
    return done.stdout


def commit(worktree: Path, name: str, content: str = "the work\n") -> str:
    """Commit one file in ``worktree`` and answer the new tip."""
    (worktree / name).write_text(content, encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git("commit", "-q", "-m", f"feat: {name}", cwd=worktree)
    return git("rev-parse", "HEAD", cwd=worktree).strip()


def commit_on(checkout: Path, branch: str, name: str) -> str:
    """Commit one file on ``branch`` in ``checkout`` and return it to the base."""
    git("checkout", "-q", branch, cwd=checkout)
    try:
        return commit(checkout, name)
    finally:
        git("checkout", "-q", BASE, cwd=checkout)


def process_started(pid: int) -> int:
    """The creation identity Linux gives a pid, as `onevcs` records it.

    Field 22 of `/proc/<pid>/stat`, counted from the last `)` because the command name is
    parenthesized; the producer is a Rust crate, whose `workspace::process_started`
    reads the same field.
    """
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return int(stat.rsplit(")", 1)[1].split()[19])


def seeded(root: Path, *, name: str = "checkout") -> Registry:
    """Seed a registered identity carrying one orphan branch, under a scratch registry.

    ``name`` is the checkout's directory name, which is the alias `onevcs` gives it. The
    session states are the caller's to add through :class:`Registry`, so a test says
    which of them it is about.
    """
    home = root / "onevcs-home"
    home.mkdir(parents=True)
    (home / "rules.yml").write_text(RULES, encoding="utf-8")
    (home / "workspaces.yml").write_text(UNPOOLED, encoding="utf-8")
    seed = root / f"{name}-seed"
    git("init", "-q", "-b", BASE, str(seed), cwd=root)
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "init", cwd=seed)
    origin = root / f"{name}-origin.git"
    git("clone", "-q", "--bare", str(seed), str(origin), cwd=root)
    checkout = root / name
    git("clone", "-q", str(origin), str(checkout), cwd=root)
    git("checkout", "-q", "-b", ORPHAN_BRANCH, cwd=checkout)
    commit(checkout, "orphan.txt")
    git("checkout", "-q", BASE, cwd=checkout)
    environment = {**os.environ, "ONEVCS_HOME": str(home)}
    registry = Registry(
        home=home,
        checkout=checkout,
        identity=str(origin).removesuffix(".git"),
        environment=environment,
    )
    registered = registry.run("just", "register-repo", str(checkout))
    assert registered.returncode == 0, registered.stderr + registered.stdout
    return registry
