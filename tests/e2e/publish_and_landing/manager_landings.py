"""What the manager-landing journeys share: an estate a landing is supervised in.

A manager landing — `just publish-branch`, `just repo-recover`, `just integrate` — is the
pinned engine's supervised work of the manager session that started it, from before its
first side effect to its ending. Proving that takes the manager's whole surface: the
recipe that starts it, `just watch --flow` that wakes its owner, the `Stop` entry
`.claude/settings.json` declares, and the `just unwatched` / `just unpublished`
acknowledgements that must not hide it. Everything here drives those through the real
recipes and the installed engine, against registered scratch repositories with bare
origins on disk, and holds a landing mid-operation at a **handshake**: the repository's
own `pre-push` hook, which records its arrival and waits for a file the journey writes —
so a journey says when the landing moves, and a timeout only ever guards a hang.

Two boundaries are substituted and no others: the paid model of the originating run, by
`tests/e2e/fake_backend.py` and `tests/e2e/fake_codex.py` exactly as every launch journey
here substitutes it, and GitHub, by `tests/e2e/fake_gh.py` at `onevcs`'s `ONEVCS_GH` seam.

Each journey reads the landing's own journal (`<runs>/.flows/<flow>/landings/<id>/`) for two
things no view reports: a handshake on a recorded event, and which operation's handle an
event carries. Every claim about what a manager is shown is made through the recipes.
"""

# The finding this answers is which Nx project owns this file: `publish-and-landing`, the
# directory-owned project whose key is the recipes, wrappers and configs every landing
# journey here drives, and the only project whose journeys import this module.
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, NewType

import short_state
from conftest import OWNERSHIP_ENV, git
from fake_backend import PROMPT_LOG_ENV, RUN_ON_MARKER_ENV
from project_fixtures import helper, project_from_plan
from scratch_identity import GIT_IDENTITY, Identity, seeded
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.root import REPO_ROOT

#: A manager session, as the harness names one and the Stop payload carries it.
Session = NewType("Session", str)

#: The manager whose run left the branches, and the managers who land them.
ORIGINATOR = Session("manager-originator")

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
FAKE_GH = helper("fake_gh.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

#: Exit statuses of `watch --flow`, named as the engine's `docs/stop-guard.md` names them.
WATCH_PROGRESSED = 0
WATCH_ELAPSED = 5
WATCH_FLOW_FAILED = 9

#: The longest any landing a journey starts, or any push it holds, may live: a hang guard,
#: enforced outside the test process so that one killed before its teardown leaves
#: nothing running past it.
LIFETIME = int(e2e_timeout(900))

#: The `pre-push` hook every checkout of an estate runs: it records each arrival, waits for
#: the journey's word, and then passes or refuses as the journey said.
#: It holds for at most ``bound`` seconds and then refuses, so a hold nobody released — a
#: journey killed before its teardown ran — never keeps a push, or the repository's lock
#: behind it, waiting for ever.
HELD_PRE_PUSH = """#!/bin/sh
echo "$$" >> '{hold}/arrivals'
arrival=$(wc -l < '{hold}/arrivals' | tr -d ' ')
deadline=$(( $(date +%s) + {bound} ))
while [ ! -f '{hold}/go' ] && [ ! -f "{hold}/go-$arrival" ]; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo 'gate: held past its bound with nobody releasing it' >&2
        exit 1
    fi
    sleep 0.1
done
if [ -f '{hold}/refuse' ]; then
    echo 'gate: the repository refuses this push' >&2
    exit 1
fi
exit 0
"""


@dataclass(frozen=True)
class Hold:
    """The handshake a held `pre-push` hook waits on."""

    directory: Path

    def arrivals(self) -> int:
        """How many pushes have reached the hook so far."""
        arrivals = self.directory / "arrivals"
        return len(arrivals.read_text().split()) if arrivals.exists() else 0

    def release(self, *, refuse: bool = False) -> None:
        """Let every held push go on, refused by the repository's gate where asked."""
        if refuse:
            (self.directory / "refuse").touch()
        (self.directory / "go").touch()

    def release_arrival(self, arrival: int) -> None:
        """Let the ``arrival``-th push to reach the hook go on, holding every other."""
        (self.directory / f"go-{arrival}").touch()

    def rearm(self) -> None:
        """Hold the next push again, passing unless told otherwise."""
        for path in self.directory.iterdir():
            path.unlink()


class HeldReport:
    """A standard output the journey holds full, so the landing's report waits on the journey.

    `onepipeline integrate` writes its train's report to standard output after `onevcs` has
    recorded the train reaching its base and before the engine records the landing's
    ending, so a landing handed this pipe stops there — between those two milestones —
    until the journey drains it. The pipe is filled to capacity before the landing starts,
    which makes any write the landing makes to it wait; nothing it writes earlier goes to
    standard output, so the hold falls exactly there.
    """

    def __init__(self) -> None:
        self.read_end, write_end = os.pipe()
        os.set_blocking(write_end, False)
        filled = 0
        for chunk in (b"." * 4096, b"."):
            while True:
                try:
                    filled += os.write(write_end, chunk)
                except BlockingIOError:
                    break
        os.set_blocking(write_end, True)
        self.write_end: int | None = write_end
        #: How many bytes of filler precede what the landing writes.
        self.filler = filled

    def hand_over(self) -> int:
        """The descriptor the landing is started with; this process's copy is closed after."""
        assert self.write_end is not None
        return self.write_end

    def handed(self) -> None:
        """Close this process's copy of the write end, so draining ends at the landing's exit."""
        if self.write_end is not None:
            os.close(self.write_end)
            self.write_end = None

    def drain(self) -> str:
        """Release the landing's report, and answer what the landing wrote after the filler."""
        self.handed()
        with os.fdopen(self.read_end, "rb") as pipe:
            return pipe.read()[self.filler :].decode()


@dataclass(frozen=True)
class Returned:
    """One `just watch --flow` ending, read off the JSON record it prints on stdout.

    The fields are the engine's own record keys (`docs/stop-guard.md`); ``line`` keeps the
    record whole for a failure message.
    """

    exit: int
    condition: str
    kind: str | None
    significance: str | None
    cursor: str
    #: The forwarded verb's status, on a flow's own ending.
    status: int | None
    #: Whether the ending is a landing's death.
    died: bool | None
    line: str
    said: str

    @classmethod
    def parse(cls, exit_status: int, line: str, said: str) -> Returned:
        record = json.loads(line)
        assert isinstance(record, dict) and record.get("watch") == "return", line
        return cls(
            exit=exit_status,
            condition=str(record["condition"]),
            kind=record.get("kind"),
            significance=record.get("significance"),
            cursor=str(record["cursor"]),
            status=record.get("status"),
            died=record.get("died"),
            line=line,
            said=said,
        )


class JournalEvent(NamedTuple):
    """One record of a landing's journal, in the fields a journey reads."""

    kind: str
    source: str
    #: The event's own payload, whose shape is its kind's.
    payload: Mapping[str, object]
    labels: Mapping[str, object]

    @classmethod
    def parse(cls, line: str) -> JournalEvent:
        record = json.loads(line)
        assert isinstance(record, dict), line
        payload, labels = record.get("payload") or {}, record.get("labels") or {}
        assert isinstance(payload, dict) and isinstance(labels, dict), line
        return cls(str(record["kind"]), str(record["source"]), payload, labels)


class LiveRun(NamedTuple):
    """A run launched here that stays live, and the identity whose registry it runs under."""

    run: str
    identity: Identity


@dataclass
class Estate:
    """One host's worth of state: registries, runs, the managers' own state, and the hold."""

    root: Path
    runs: Path
    state: Path
    hold: Hold
    base_environment: dict[str, str]
    identities: dict[str, Identity] = field(default_factory=dict)
    #: Every landing started here, so one a failed journey left running is reaped.
    landings: list[Landing] = field(default_factory=list)
    #: Every default watch a journey left armed, reaped the same way.
    armed: list[Armed] = field(default_factory=list)
    #: Every run launched here that stays live, so a failed journey still stops it.
    live_runs: list[LiveRun] = field(default_factory=list)

    def close(self) -> None:
        """Release the hold, reap every landing still running, and stop every live run.

        Each landing and armed watch is killed by the process group this journey started it
        in, and each run is stopped through `just stop` as the manager who launched it.
        """
        self.hold.release()
        for process in [landing.process for landing in self.landings] + [
            watch.process for watch in self.armed
        ]:
            if process.poll() is None:
                # The group `start_new_session` made for the process this journey spawned.
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=e2e_timeout(60))
        for live in self.live_runs:
            self.recipe(ORIGINATOR, live.identity, "stop", live.run)

    def as_manager(self, session: Session, identity: Identity) -> dict[str, str]:
        """The environment a recipe run by ``session`` against ``identity``'s registry is handed."""
        environment = {
            **self.base_environment,
            **identity.environment,
            "ONEVCS_HOME": str(identity.home),
        }
        environment["CLAUDE_CODE_SESSION_ID"] = session
        return environment

    def recipe(
        self,
        session: Session,
        identity: Identity,
        *arguments: str,
        cwd: Path = REPO_ROOT,
    ) -> subprocess.CompletedProcess[str]:
        """Run one recipe to its end as ``session``, from ``cwd``."""
        environment = self.as_manager(session, identity)
        if cwd != REPO_ROOT:
            # The recipe's `uv run` resolves this project from inside another checkout.
            environment["UV_PROJECT"] = str(REPO_ROOT)
        return subprocess.run(
            _just(cwd, *arguments),
            cwd=cwd,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )

    def acknowledgement_records(self) -> dict[str, str]:
        """Every acknowledgement either recipe has recorded, by path and contents.

        A refused acknowledgement must leave this exactly as it was: the branch records
        under the directory `just unpublished` names, and each run's own and each flow's.
        """
        found: dict[str, str] = {}
        for directory in (self.state, self.runs):
            for path in directory.rglob("*"):
                if path.is_file() and "acknowledg" in str(path.relative_to(directory)):
                    found[str(path)] = path.read_text(encoding="utf-8")
        return found


def estate(root: Path, oneharness_bin: str) -> Estate:
    """An empty estate under ``root``: runs, manager state and a held `pre-push` hook."""
    environment = {name: value for name, value in os.environ.items() if name not in OWNERSHIP_ENV}
    runs = root / "runs"
    runs.mkdir()
    state = short_state.state_home(root)
    hold = Hold(root / "hold")
    hold.directory.mkdir()
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs)
    environment["XDG_STATE_HOME"] = str(state)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    environment[PROMPT_LOG_ENV] = str(root / "turns.jsonl")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    return Estate(root=root, runs=runs, state=state, hold=hold, base_environment=environment)


def held_identity(estate: Estate, name: str) -> Identity:
    """A `local-direct` identity whose every push waits at the estate's hold."""
    identity = seeded(estate.root / name, execution=f"{name}-execution", publication=name)
    hooks = estate.root / f"{name}-hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text(
        HELD_PRE_PUSH.format(hold=estate.hold.directory, bound=LIFETIME), encoding="utf-8"
    )
    hook.chmod(0o755)
    for checkout in (identity.publication, identity.execution):
        git("config", "core.hooksPath", str(hooks), cwd=checkout)
    estate.identities[name] = identity
    return identity


#: A hosted identity's rules: every change request it opens lands by the host's own merge.
HOSTED_RULES = """version: 3
trailer_prefix: Orchestrator-
rules: []
default:
  publication: {publication}
  approvals: {approvals}
"""
#: The origin a hosted identity is registered under: `tests/e2e/fake_gh.py`'s repository.
HOSTED_ORIGIN = "https://github.com/acme-corp/hosted.git"


@dataclass(frozen=True)
class Hosted:
    """A `change-auto` identity whose host is `tests/e2e/fake_gh.py`, and that host's levers."""

    identity: Identity
    #: The check script the host reports; rewritten to move a check.
    checks: Path
    #: Written to let the host merge the change it armed.
    merge_permit: Path

    def report(self, *lines: str) -> None:
        self.checks.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def hosted_identity(
    estate: Estate,
    name: str,
    *checks: str,
    publication: str = "change-auto",
    approvals: str = "none",
) -> Hosted:
    """A registered hosted identity, offline: git pushes to a bare origin on disk and every
    `gh` call reaches the host double. `change-auto` with no approvals unless named."""
    root = estate.root / name
    root.mkdir()
    home = root / "onevcs"
    home.mkdir()
    (home / "rules.yml").write_text(
        HOSTED_RULES.format(publication=publication, approvals=approvals), encoding="utf-8"
    )
    seed, origin, checkout = root / "seed", root / "origin.git", root / name
    git("init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    git("clone", "-q", "--bare", str(seed), str(origin))
    git("clone", "-q", str(origin), str(checkout))
    state = root / "gh-state"
    script = root / "checks"
    environment = {
        # llmlint: ignore[e2e_not_mocked] Only the host's decisioning, at onevcs's own seam.
        "ONEVCS_GH": str(FAKE_GH),
        "FAKE_GH_STATE": str(state),
        "FAKE_GH_CALLS": str(root / "gh-calls.jsonl"),
        "FAKE_GH_ORIGIN": str(origin),
        "FAKE_GH_CHECKS": str(script),
        # How long the publication watches the host's checks: a hang guard, never a wait.
        "ONEVCS_CHECKS_TIMEOUT_SECONDS": "900",
    }
    identity = Identity(
        publication=checkout, execution=checkout, home=home, environment=environment
    )
    hosted = Hosted(identity=identity, checks=script, merge_permit=state / "merge")
    hosted.report(*checks)
    registered = estate.recipe(
        ORIGINATOR, identity, "register-repo", str(checkout), "--origin", HOSTED_ORIGIN
    )
    assert registered.returncode == 0, registered.stdout + registered.stderr
    estate.identities[name] = identity
    return hosted


def sibling_identity(estate: Estate, beside: Identity, name: str) -> Identity:
    """A second identity registered in ``beside``'s registry, with an origin of its own."""
    root = estate.root / name
    root.mkdir()
    origin, seed, checkout = root / "origin.git", root / "seed", root / name
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text(f"{name}\n", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=seed)
    git("remote", "add", "origin", str(origin), cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    git("clone", "-q", str(origin), str(checkout))
    identity = Identity(publication=checkout, execution=checkout, home=beside.home, environment={})
    registered = estate.recipe(ORIGINATOR, identity, "register-repo", str(checkout))
    assert registered.returncode == 0, registered.stdout + registered.stderr
    estate.identities[name] = identity
    return identity


def preserved_by_session(
    estate: Estate, identity: Identity, branch: str, launcher: Session
) -> None:
    """Leave ``branch`` preserved in ``identity`` by a session ``launcher`` opened and closed."""
    environment = estate.as_manager(launcher, identity)

    def onevcs(*arguments: str) -> str:
        ran = subprocess.run(
            ["uv", "run", "onevcs", *arguments],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        assert ran.returncode == 0, ran.stdout + ran.stderr
        return ran.stdout

    opened = json.loads(
        onevcs(
            "session",
            "open",
            str(identity.publication),
            "--branch",
            branch,
            "--label",
            f"launcher={launcher}",
        )
    )
    worktree = Path(opened["worktree"])
    (worktree / "kept.txt").write_text("kept\n", encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git(*GIT_IDENTITY, "commit", "-qm", "feat: kept work", cwd=worktree)
    onevcs("session", "close", opened["token"])


def originating_run(
    estate: Estate,
    identity: Identity,
    run: str,
    subjects: Mapping[str, str],
    *,
    waiting_on_a_person: bool = False,
) -> dict[str, str]:
    """Launch a real run as :data:`ORIGINATOR` that leaves one preserved branch per node.

    ``subjects`` maps a node id to the commit subject its worker leaves on its branch — an
    `(incomplete step)` one is a branch `just repo-recover` lands. Every node declares
    `publish: preserve`, so the run settles with each branch kept on its origin and owed a
    landing. Answers each node's branch, as the run's own result names it.

    ``waiting_on_a_person`` adds a human action nobody attests, launched detached, so the
    run stays live under its driver once every branch is preserved; each branch is then
    answered as the default branch template names it, `<run>/<node>`, once its origin holds
    it. The caller stops that run.
    """
    commands: dict[str, list[list[str]]] = {}
    tasks: list[dict[str, object]] = []
    for node, subject in subjects.items():
        marker = f"Leave the file {node}.txt behind on your branch, and commit it."
        commands[marker] = [
            [
                "sh",
                "-c",
                f"[ -f {node}.txt ] || {{ echo {node} > {node}.txt && git add {node}.txt && "
                f"git {' '.join(GIT_IDENTITY)} commit -qm '{subject}'; }}",
            ]
        ]
        tasks.append(
            {
                "id": node,
                "repo": str(identity.publication),
                "execution_checkout": identity.execution.name,
                "persona": "engineer",
                "publish": "preserve",
                "title": subject.removesuffix(" (incomplete step)"),
                "task": (
                    f"## What\n{marker}\n\n## Why\nA branch a manager lands later.\n\n"
                    "## Acceptance criteria\n- The dispatch starts and reports.\n"
                ),
            }
        )
    if waiting_on_a_person:
        tasks.append({"id": "sign-off", "kind": "human", "task": "Sign off on the landing."})
    keyed = estate.root / f"{run}-commands.json"
    keyed.write_text(json.dumps(commands), encoding="utf-8")
    plan = estate.root / f"{run}.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Leave branches a manager lands later"},
                "name": run,
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    environment = estate.as_manager(ORIGINATOR, identity)
    environment[RUN_ON_MARKER_ENV] = str(keyed)
    estate.hold.release()
    detach = ["--detach"] if waiting_on_a_person else []
    launched = subprocess.run(
        ["just", "orchestrate", project_from_plan(plan), *detach],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    assert launched.returncode == 0, f"{launched.stdout}\n{launched.stderr}"
    if waiting_on_a_person:
        estate.live_runs.append(LiveRun(run, identity))
        branches = {node: f"{run}/{node}" for node in subjects}
        origin = identity.publication.parent / "origin.git"

        def preserved() -> bool:
            heads = git("ls-remote", "--heads", str(origin))
            return all(f"refs/heads/{branch}" in heads for branch in branches.values())

        until("the live run to preserve its branches", preserved, seconds=600, state=lambda: "")
        estate.hold.rearm()
        return branches
    estate.hold.rearm()
    # llmlint: ignore[tests_mirror_real_usage] No view answers a settled node's branch alone.
    result = json.loads((estate.runs / run / "result.json").read_text(encoding="utf-8"))
    branches = {str(node["id"]): str(node["branch"]) for node in result["nodes"]}
    assert {str(node["outcome"]) for node in result["nodes"]} == {"preserved"}, result
    return branches


def fetched(identity: Identity, branches: Sequence[str]) -> None:
    """Make preserved branches local branches of the publication checkout, as a person does.

    `onevcs integrate` merges branches of the checkout it is run inside.
    """
    for branch in branches:
        git("fetch", "-q", "origin", f"{branch}:{branch}", cwd=identity.publication)


def _just(cwd: Path, *arguments: str) -> list[str]:
    """`just` from ``cwd``, naming this checkout's justfile wherever that is."""
    if cwd == REPO_ROOT:
        return ["just", *arguments]
    return [
        "just",
        "--justfile",
        str(REPO_ROOT / "justfile"),
        "--working-directory",
        str(cwd),
        *arguments,
    ]


class Landing:
    """One landing recipe running as a manager, in a process group this journey owns."""

    def __init__(
        self,
        estate: Estate,
        session: Session,
        identity: Identity,
        arguments: Sequence[str],
        *,
        cwd: Path = REPO_ROOT,
        extra: Mapping[str, str] | None = None,
        report: HeldReport | None = None,
    ) -> None:
        self.estate = estate
        self.session = session
        self._out = estate.root / f"landing-{len(list(estate.root.glob('landing-*.err')))}"
        self.stdout_path = self._out.with_suffix(".out")
        self.stderr_path = self._out.with_suffix(".err")
        environment = {**estate.as_manager(session, identity), **(extra or {})}
        if cwd != REPO_ROOT:
            # The recipe's `uv run` resolves this project from inside another checkout.
            environment["UV_PROJECT"] = str(REPO_ROOT)
        with self.stdout_path.open("w") as out, self.stderr_path.open("w") as err:
            # Under coreutils `timeout`, which leads the new session and kills its whole
            # process group at the bound, so the landing cannot outlive a journey that
            # died before its teardown.
            self.process = subprocess.Popen(
                ["timeout", "--kill-after=30", str(LIFETIME), *_just(cwd, *arguments)],
                cwd=cwd,
                env=environment,
                stdout=out if report is None else report.hand_over(),
                stderr=err,
                text=True,
                start_new_session=True,
            )
        if report is not None:
            report.handed()
        estate.landings.append(self)
        self.flow = ""
        self.landing = ""
        until(
            "the landing to register in its manager's flow",
            self._registered,
            seconds=120,
            state=self.said,
        )

    def said(self) -> str:
        return self.stdout_path.read_text() + self.stderr_path.read_text()

    def _registered(self) -> bool:
        for line in self.stderr_path.read_text().splitlines():
            # The verb's own receipt: `landing <ID> of flow <FLOW>: <verb> registered …`.
            if line.startswith("landing ") and " of flow " in line:
                words = line.split()
                self.landing, self.flow = words[1], words[4].rstrip(":")
                return True
        if self.process.poll() is not None:
            raise AssertionError(f"the landing exited before registering:\n{self.said()}")
        return False

    def journal(self) -> list[JournalEvent]:
        """The landing's own journal, for handshakes and the handle each event carries."""
        path = self.directory() / "events.jsonl"
        # llmlint: ignore[tests_mirror_real_usage] No view answers which handle an event carries.
        lines = path.read_text().splitlines() if path.exists() else []
        return [JournalEvent.parse(line) for line in lines]

    def directory(self) -> Path:
        return self.estate.runs / ".flows" / self.flow / "landings" / self.landing

    def operation(self) -> str:
        """The `onevcs` operation handle the engine prepared for this landing."""
        record = json.loads((self.directory() / "operation.json").read_text(encoding="utf-8"))
        return str(record["operation_id"])

    def recorded(self, kind: str, **payload: object) -> bool:
        """Whether the journal holds a ``kind`` event whose payload carries every given field."""
        return any(
            event.kind == kind
            and all(event.payload.get(name) == value for name, value in payload.items())
            for event in self.journal()
        )

    def await_recorded(self, kind: str, **payload: object) -> None:
        until(
            f"the landing to record {kind} {payload}",
            lambda: self.recorded(kind, **payload),
            seconds=300,
            state=lambda: f"{self.kinds()}\n{self.said()}",
        )

    def kinds(self) -> list[str]:
        return [event.kind for event in self.journal()]

    def ended(self) -> int:
        """Wait for the recipe to exit, and answer its status."""
        try:
            return self.process.wait(timeout=e2e_timeout(300))
        except subprocess.TimeoutExpired:
            self.kill()
            raise AssertionError(f"the landing hung:\n{self.said()}") from None

    def kill(self) -> None:
        """Kill the landing's own process group, which this journey started and nothing else.

        The group is the one `start_new_session` made for the process this journey spawned,
        so its id is that process's own pid, captured when it started.
        """
        os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=e2e_timeout(60))


def _watch_command(flow: str, arguments: Sequence[str]) -> list[str]:
    return ["just", "watch", "--flow", flow, *arguments]


def _returned(exit_status: int, stdout: str, stderr: str) -> Returned:
    lines = [line for line in stdout.splitlines() if line.startswith("{")]
    assert lines, f"the watch printed no ending record (exit {exit_status}):\n{stdout}\n{stderr}"
    return Returned.parse(exit_status, lines[-1], stderr)


def watch(
    estate: Estate, session: Session, identity: Identity, flow: str, *arguments: str
) -> Returned:
    """One `just watch --flow` as ``session``, run to its ending."""
    ran = subprocess.run(
        _watch_command(flow, arguments),
        cwd=REPO_ROOT,
        env=estate.as_manager(session, identity),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )
    return _returned(ran.returncode, ran.stdout, ran.stderr)


class Armed:
    """A default `just watch --flow` left armed while the journey moves the landing on."""

    def __init__(
        self, estate: Estate, session: Session, identity: Identity, flow: str, *arguments: str
    ) -> None:
        self.process = subprocess.Popen(
            _watch_command(flow, ["--timeout", "600", *arguments]),
            cwd=REPO_ROOT,
            env=estate.as_manager(session, identity),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        estate.armed.append(self)

    def waiting(self) -> bool:
        return self.process.poll() is None

    def returned(self) -> Returned:
        stdout, stderr = self.process.communicate(timeout=e2e_timeout(600))
        return _returned(self.process.returncode, stdout, stderr)


def woken_until_ended(
    estate: Estate, session: Session, identity: Identity, flow: str, cursor: str
) -> tuple[list[Returned], Returned]:
    """Re-arm the default watch from ``cursor`` until the flow's own ending; every wake."""
    woken: list[Returned] = []
    for _ in range(50):
        returned = watch(estate, session, identity, flow, "--timeout", "300", "--cursor", cursor)
        if returned.condition != "landing-progress":
            return woken, returned
        woken.append(returned)
        cursor = returned.cursor
    raise AssertionError(f"the flow never ended: {[wake.kind for wake in woken]}")


def stop_hook(estate: Estate, session: Session, identity: Identity) -> str | None:
    """Run the declared `Stop` entry for ``session``; the reason it blocks with, or None."""
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    [command] = [hook["command"] for entry in settings["hooks"]["Stop"] for hook in entry["hooks"]]
    payload = {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": False}
    answered = subprocess.run(
        ["/bin/sh", "-c", command],
        input=json.dumps(payload),
        cwd=REPO_ROOT,
        env={**estate.as_manager(session, identity), "CLAUDE_PROJECT_DIR": str(REPO_ROOT)},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert answered.returncode == 0, answered
    if not answered.stdout.strip():
        return None
    decision = json.loads(answered.stdout)
    return str(decision["reason"]) if decision.get("decision") == "block" else None
