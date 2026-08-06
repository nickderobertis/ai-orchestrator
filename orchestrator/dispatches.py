"""What every live dispatch on this host is running *right now*, proven by ownership.

`orchestrator.liveness` answers the same question one level up — is a launched
orchestrator working — and `orchestrator.activity` answers it one level in, from what
a streamed turn publishes about itself. Neither can say which *side* of the onejudge
conversation is running, on which harness identity, or for how long. A planner who
needed that has had one tool: matching `ps` output by pattern, which counted six live
dispatches where there were two and missed a judge turn wedged for one hour
fifty-four minutes entirely.

This module is the answer that does not guess. Its candidate set is the dispatch
ownership registry the scratch sweep already trusts:
`ORCHESTRATOR_AGENT_STATUS_DIR`, the stamp the kernel fixes into the environment of
everything a dispatch starts and no process can shed, paired with the owner lock that
says a dispatcher still holds that directory. A process is this harness's only when
both agree; nothing is ever recognised by the shape of its command line alone.

What the command line *is* used for, once ownership is established, is telling one
turn from another inside a dispatch that owns them all: an agent turn, its
simulated-user judge, and an llmlint tier are three different invocations of one
harness under one stamp, and the config each names is what distinguishes them. The
same applies to the harness identity serving a turn — oneharness selects it by
falling through a chain, and the credential directory it hands the provider is where
that selection becomes observable from outside.

Every read degrades. An unreadable procfs, a scratch root that is not the
dispatchers', a stamp naming nothing this host is running — each answers "not known"
rather than raising, and a view that knows nothing reports what it reported before
this module existed.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, get_args

from .coordination import proc_root
from .labels import LABEL_ENV, MAX_VALUE_CODEPOINTS, SMOKE_LABEL, AgentRole, parse_labels
from .redaction import redact
from .scratch import (
    AGENT_STATUS_DIR_ENV,
    AGENT_STATUS_DIR_NAME,
    WATCHDOG_PREFIX,
    watchdog_has_a_live_owner,
)

#: What a dispatched process may be serving. Built *from* `orchestrator.labels`'
#: `AgentRole` rather than restating it, because that is the vocabulary a dispatch
#: actually stamps: a role added there has to arrive here, and a copy would report it
#: as "role unknown" with nothing failing. The two extras are turns rather than
#: dispatch roles — an llmlint tier and a smoke run carry no `agent_role` label of
#: their own and are recognised from the invocation and the `smoke` label instead.
DispatchRole = AgentRole | Literal["llmlint", "smoke"]
DISPATCH_ROLES: frozenset[str] = frozenset(get_args(AgentRole)) | {"llmlint", "smoke"}

#: How long a turn of each role ordinarily runs on this host. These are not budgets
#: and nothing is stopped for exceeding one: they are the scale a duration is read
#: against, so "23m" can be reported as ordinary for a worker and alarming for a
#: judge. The worker figure is the upper end of the 600-2000s first turns AGENTS.md
#: documents; the supervisory roles answer one bounded question per turn and are an
#: order of magnitude below it.
TYPICAL_TURN_SECONDS: Mapping[str, float] = {
    "worker": 2000.0,
    "judge": 300.0,
    "llmlint": 900.0,
    "pr-author": 600.0,
    "smoke": 300.0,
    "orchestrator": 2000.0,
    "check-in": 300.0,
}
#: How many times its role's typical duration a turn may run before a view calls it
#: anomalous. Deliberately generous: a flagged turn asks a planner to go and look, so
#: the cost of a false one is a wasted investigation and the cost of a missed one is
#: the wedged judge this exists to catch. Three puts a judge's threshold at fifteen
#: minutes and a worker's at one hour forty, both far past their real spread.
OUTLIER_TURN_MULTIPLE = 3.0
#: A turn whose role is unknown is timed against the longest typical duration here,
#: so an unclassified turn is never flagged before a worker doing the same thing
#: would be.
_UNKNOWN_TYPICAL_SECONDS = max(TYPICAL_TURN_SECONDS.values())

#: The process states the kernel counts toward the load average: runnable, and
#: uninterruptible (a task blocked in the kernel, which is what a compile or a git
#: object walk under I/O pressure looks like).
_LOADED_STATES = frozenset({"R", "D"})

#: What a live process's command line has to name for a turn to be attributed to a
#: role. Each is a harness *configuration* rather than a binary: one dispatch runs
#: agent, judge, and llmlint turns through the same `oneharness` executable under one
#: ownership stamp, and the config it is pointed at is the only thing that separates
#: them. Ordered longest-evidence-first so `oneharness.judge.toml` is never read as
#: the agent side's `oneharness.toml`.
_ROLE_EVIDENCE: tuple[tuple[str, DispatchRole], ...] = (
    ("oneharness.judge.toml", "judge"),
    ("oneharness.llmlint.toml", "llmlint"),
    ("llmlint-oneharness.sh", "llmlint"),
    ("llmlint-judge.sh", "llmlint"),
    ("oneharness-orchestrator.sh", "orchestrator"),
    ("oneharness.orchestrator.toml", "orchestrator"),
    ("oneharness-agent.sh", "worker"),
)

#: The credential directory each Claude identity is given, by the variable
#: `oneharness.toml` maps into that variant's child alone. The value is a path
#: derived from ``HOME``, so the identity is read from the directory *name* rather
#: than from a literal this repository would have to keep in step with a host.
#:
#: `scripts/claude-alt-config-dir.sh` and `scripts/codex-alt-home.sh` are those names'
#: one source, and this module cannot read them: it runs in a view process that never
#: sourced either wrapper, so it has to recognise the directory a *dispatched* process
#: was given. `tests/test_dispatches.py` is therefore the drift gate — renaming one
#: there without renaming it here would make every alternate2 turn report as
#: `claude-code:primary`, a wrong positive claim nothing else would catch.
_CLAUDE_CONFIG_ENV = "CLAUDE_CONFIG_DIR"
_CODEX_HOME_ENV = "CODEX_HOME"
_ALTERNATE_CLAUDE_DIRS: Mapping[str, str] = {
    ".claude-alt": "claude-code:alternate",
    ".claude-alt2": "claude-code:alternate2",
}
_ALTERNATE_CODEX_DIR = ".codex-alt"
#: The provider executables an identity can be reported for. A stamped process that
#: is neither is some other part of the dispatch — a shell, `git`, `uv`, a gate — and
#: says nothing about which subscription is serving the turn.
_PROVIDER_BINARIES: Mapping[str, str] = {"claude": "claude-code", "codex": "codex"}


@dataclass(frozen=True)
class _ProcessRecord:
    """One live process, as much of it as this user is entitled to read."""

    pid: int
    started_at: float | None
    state: str
    cpu_seconds: float
    argv: tuple[str, ...]
    environ: Mapping[str, str]


@dataclass(frozen=True)
class LiveTurn:
    """The turn a live dispatch is serving, and what is serving it."""

    role: DispatchRole | None
    harness: str | None
    started_at: float | None
    #: The multiple of its role's typical duration this turn is judged against. Held
    #: per turn rather than read from the module constant at each call site, so a view
    #: told to use a different threshold uses it everywhere it renders that turn.
    outlier_multiple: float = OUTLIER_TURN_MULTIPLE

    def age(self, *, now: float) -> float | None:
        return None if self.started_at is None else max(0.0, now - self.started_at)

    def typical_seconds(self) -> float:
        return TYPICAL_TURN_SECONDS.get(self.role or "", _UNKNOWN_TYPICAL_SECONDS)

    def is_outlier(self, *, now: float) -> bool:
        """Whether this turn has run past a generous multiple of its role's scale."""
        age = self.age(now=now)
        return age is not None and age > self.typical_seconds() * self.outlier_multiple

    def describe(self, *, now: float) -> str:
        role = self.role or "role unknown"
        harness = self.harness or "harness unknown"
        age = self.age(now=now)
        if age is None:
            return f"{role} on {harness}, turn age unknown"
        elapsed = int(age)
        timing = f"turn running {elapsed // 60}m{elapsed % 60:02d}s"
        if self.is_outlier(now=now):
            typical = int(self.typical_seconds())
            timing += f" — ANOMALOUS, past {self.outlier_multiple:g}x the {typical}s typical for it"
        return f"{role} on {harness}, {timing}"


@dataclass(frozen=True)
class LiveDispatch:
    """One dispatch a live dispatcher still holds, and where in a graph it sits."""

    status_dir: Path
    run_id: str | None
    round: str | None
    node: str | None
    step: str | None
    persona: str | None
    launcher: str | None
    turn: LiveTurn
    #: Processes in this dispatch's tree the kernel is counting toward the load
    #: average right now. This is the load attribution: a `just gate` or a pytest
    #: suite run by a dispatched worker is in that worker's tree and carries its
    #: stamp, so the run and node responsible for it are named rather than inferred.
    runnable: int
    cpu_seconds: float
    processes: int

    @property
    def locator(self) -> tuple[str, str] | None:
        """The ``(round, node)`` key the run journal and `activity` both join on."""
        if self.run_id is None or self.round is None or not self.node:
            return None
        return self.round, self.node

    def where(self) -> str:
        """One phrase naming this dispatch's place in a graph, however much is known."""
        if self.node is None:
            return f"{self.persona or 'dispatch'} (no graph locator)"
        node = f"{self.node}[{self.step}]" if self.step else self.node
        run = f"{self.run_id} " if self.run_id else ""
        round_number = f"round-{int(self.round):02d} " if self.round else ""
        return f"{run}{round_number}{node}"


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def _environment(raw: bytes) -> dict[str, str]:
    """The NUL-delimited environment procfs holds, as whole entries.

    Split on ``NUL`` and partitioned at the first ``=`` rather than scanned for a
    substring, exactly as the sweep reads the same file: a value that merely
    *contains* a variable's name is not that variable, and a dispatch whose stamp
    were matched that loosely would claim processes it never started.
    """
    environ: dict[str, str] = {}
    for entry in _text(raw).split("\0"):
        name, separator, value = entry.partition("=")
        if separator and name and name not in environ:
            environ[name] = value
    return environ


def _clock_ticks() -> float:
    try:
        ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):  # pragma: no cover - every Linux defines it
        return 100.0
    return float(ticks) if ticks and ticks > 0 else 100.0


def _boot_time(root: Path) -> float | None:
    """This host's boot instant, which turns a process's start tick into a clock time."""
    try:
        raw = (root / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in raw.splitlines():
        if line.startswith("btime "):
            value = line.split()[1:2]
            if value and value[0].isdigit():
                return float(value[0])
    return None


def _record(
    root: Path, pid: int, *, boot_time: float | None, ticks: float
) -> _ProcessRecord | None:
    """Read one live process, or ``None`` when it is gone or another user's."""
    directory = root / str(pid)
    try:
        stat = (directory / "stat").read_text(encoding="utf-8", errors="replace")
        environ = _environment((directory / "environ").read_bytes())
    except OSError:
        return None
    # Fields after the parenthesized comm — which may itself hold spaces and
    # parentheses — begin at state (field 3); utime/stime are 14/15 and starttime 22.
    fields = stat[stat.rfind(")") + 2 :].split()
    if len(fields) < 20:
        return None
    state = fields[0]
    try:
        cpu_seconds = (int(fields[11]) + int(fields[12])) / ticks
        started_at = None if boot_time is None else boot_time + int(fields[19]) / ticks
    except ValueError:  # pragma: no cover - procfs numerics
        return None
    try:
        argv = tuple(
            part for part in _text((directory / "cmdline").read_bytes()).split("\0") if part
        )
    except OSError:
        argv = ()
    return _ProcessRecord(pid, started_at, state, cpu_seconds, argv, environ)


def _stamped_status_dir(environ: Mapping[str, str], scratch_root: Path) -> Path | None:
    """The dispatch status directory this process's environment names, if it names one.

    The same shape `orchestrator.scratch` requires of the stamp — exactly
    ``<scratch root>/orchestrator-watchdog-*/agent`` — so a value that lands anywhere
    else under a shared root proves nothing about who started the process carrying it.
    """
    value = environ.get(AGENT_STATUS_DIR_ENV)
    if not value:
        return None
    try:
        relative = PurePosixPath(value).relative_to(PurePosixPath(scratch_root))
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) != 2 or parts[1] != AGENT_STATUS_DIR_NAME:
        return None
    if not parts[0].startswith(WATCHDOG_PREFIX) or parts[0] == WATCHDOG_PREFIX:
        return None
    return scratch_root / parts[0] / AGENT_STATUS_DIR_NAME


def _live_processes(scratch_root: Path) -> dict[Path, list[_ProcessRecord]] | None:
    """Every readable live process carrying a dispatch stamp, grouped by that stamp.

    ``None`` means the question could not be asked at all — a procfs that cannot show
    this very process is not one any claim may be built on, the same distinction
    `orchestrator.scratch` draws before it acts on an ownership proof.
    """
    root = proc_root()
    if not (root / str(os.getpid())).is_dir():
        return None
    try:
        entries = sorted(root.iterdir())
    except OSError:  # pragma: no cover - unreadable between the self probe and here
        return None
    boot_time = _boot_time(root)
    ticks = _clock_ticks()
    grouped: dict[Path, list[_ProcessRecord]] = {}
    for entry in entries:
        if not entry.name.isdigit():
            continue
        record = _record(root, int(entry.name), boot_time=boot_time, ticks=ticks)
        if record is None:
            continue
        status_dir = _stamped_status_dir(record.environ, scratch_root)
        if status_dir is not None:
            grouped.setdefault(status_dir, []).append(record)
    return grouped


def _bounded(value: str | None) -> str | None:
    """Redact and bound one label a dispatched subprocess put in its environment."""
    if not value:
        return None
    return " ".join(redact(value).split())[:MAX_VALUE_CODEPOINTS] or None


def _labels(records: Sequence[_ProcessRecord]) -> dict[str, str]:
    """The graph labels this dispatch stamped, from whichever process still carries them.

    Every process under one dispatch inherits one value, so the first readable one is
    the dispatch's. A malformed value is not this harness's contract and is dropped
    rather than parsed leniently: `orchestrator.labels` is the only writer.
    """
    for record in records:
        if raw := record.environ.get(LABEL_ENV):
            return parse_labels(raw)
    return {}


def _role_from_argv(argv: Sequence[str]) -> DispatchRole | None:
    """Which turn this command line is serving, or ``None`` when it names none."""
    for argument in argv:
        for evidence, role in _ROLE_EVIDENCE:
            if evidence in argument:
                return role
    return None


def _harness_identity(record: _ProcessRecord) -> str | None:
    """The harness identity a live provider process is running as, if it is one.

    oneharness picks an identity by falling through its configured chain, and the one
    place that choice becomes observable from outside the process is the credential
    directory it hands the child: `oneharness.toml` maps
    ``ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR`` into ``CLAUDE_CONFIG_DIR`` for the
    ``alternate2`` variant alone, and the primary variant unsets it outright.

    The whole command line is searched for the provider rather than only its first
    word, because a provider is rarely the first word: `claude` ships as a script, so
    the kernel puts its interpreter in ``argv[0]`` and the provider's own path one or
    two entries along. Reading only ``argv[0]`` reports every real claude-code turn as
    an unidentified `node`, which is the one answer this must not give.
    """
    provider = next(
        (
            named
            for argument in record.argv
            if (named := _PROVIDER_BINARIES.get(PurePosixPath(argument).name)) is not None
        ),
        None,
    )
    if provider is None:
        return None
    if provider == "codex":
        home = PurePosixPath(record.environ.get(_CODEX_HOME_ENV, "")).name
        return "codex:alternate" if home == _ALTERNATE_CODEX_DIR else "codex"
    configured = PurePosixPath(record.environ.get(_CLAUDE_CONFIG_ENV, "")).name
    return _ALTERNATE_CLAUDE_DIRS.get(configured, "claude-code:primary")


def _turn(
    records: Sequence[_ProcessRecord],
    dispatch_role: DispatchRole | None,
    *,
    outlier_multiple: float,
) -> LiveTurn:
    """The turn these processes are serving: its role, its harness, and when it began.

    The role a *command line* names wins over the one the dispatch was launched under,
    because they answer different questions: a worker dispatch running its judge turn
    is stamped ``agent_role=worker`` for its whole life, and "what is running now" is
    the judge. When nothing names a turn, the dispatch's own role is the honest
    answer, which is what a dispatch whose provider is a test backend reports.
    """
    serving = [record for record in records if _role_from_argv(record.argv) is not None]
    role: DispatchRole | None = dispatch_role
    if serving:
        # The most recently started process naming a role is the turn in flight: an
        # agent wrapper that spawned a judge under it is the previous question.
        newest = max(serving, key=lambda record: (record.started_at or 0.0, record.pid))
        role = _role_from_argv(newest.argv)
    harnesses = [identity for record in records if (identity := _harness_identity(record))]
    starts = [record.started_at for record in (serving or records) if record.started_at is not None]
    return LiveTurn(
        role=role,
        harness=next(iter(harnesses), None),
        started_at=max(starts, default=None),
        outlier_multiple=outlier_multiple,
    )


def _dispatch(
    status_dir: Path, records: Sequence[_ProcessRecord], *, outlier_multiple: float
) -> LiveDispatch:
    labels = _labels(records)
    recorded_role = labels.get("agent_role")
    dispatch_role: DispatchRole | None = (
        # `DISPATCH_ROLES` is a runtime frozenset built from the Literal, so mypy
        # cannot relate membership in it back to the Literal's members; the check is
        # exactly the narrowing it cannot express.
        recorded_role if recorded_role in DISPATCH_ROLES else None  # type: ignore[assignment]
    )
    # `orchestrator.smoke` is the one dispatch with no persona to derive a role from:
    # it spends a real harness turn against a throwaway directory, and the label it
    # stamps to find its own history record is what names it here.
    if SMOKE_LABEL in labels:
        dispatch_role = "smoke"
    round_label = labels.get("round", "")
    return LiveDispatch(
        status_dir=status_dir,
        run_id=_bounded(labels.get("run_id")),
        round=str(int(round_label)) if round_label.isdigit() and int(round_label) >= 1 else None,
        node=_bounded(labels.get("node")),
        step=_bounded(labels.get("step")),
        persona=_bounded(labels.get("persona")),
        launcher=_bounded(labels.get("launcher")),
        turn=_turn(records, dispatch_role, outlier_multiple=outlier_multiple),
        runnable=sum(1 for record in records if record.state in _LOADED_STATES),
        cpu_seconds=sum(record.cpu_seconds for record in records),
        processes=len(records),
    )


def live_dispatches(
    *, root: Path | None = None, outlier_multiple: float = OUTLIER_TURN_MULTIPLE
) -> list[LiveDispatch] | None:
    """Every dispatch on this host a live dispatcher still owns, newest turn first.

    ``None`` means the ownership registry could not be consulted, which every caller
    has to keep distinct from an empty list: the first says nothing is known and the
    second says nothing is running. Reporting a working node as dead is the error
    this module exists to stop making, so no view may collapse the two.
    """
    scratch_root = (root or Path(tempfile.gettempdir())).resolve()
    grouped = _live_processes(scratch_root)
    if grouped is None:
        return None
    found: list[LiveDispatch] = []
    for status_dir, records in sorted(grouped.items()):
        # A watchdog-shaped directory under a shared root is a shape, not a claim; the
        # held owner lock is the claim. Requiring it here is what keeps a directory
        # anything could have left behind — and a dispatch already over, whose orphans
        # the sweep has not yet reaped — out of a picture of what is running.
        if not watchdog_has_a_live_owner(status_dir.parent):
            continue
        found.append(_dispatch(status_dir, records, outlier_multiple=outlier_multiple))
    return sorted(found, key=lambda item: (-(item.turn.started_at or 0.0), str(item.status_dir)))


#: How long a node the ledger has recorded as started may go without any live
#: dispatch carrying its stamp before a view calls it undriven. It covers exactly one
#: gap: a dispatcher that has journalled the start and not yet `exec`ed the tree that
#: carries the stamp. That is a spawn, measured in milliseconds; a minute is orders of
#: magnitude beyond it and still far inside the shortest turn this host runs.
UNDRIVEN_AFTER_SECONDS = 60.0


def undriven_locators(
    dispatches: Sequence[LiveDispatch] | None,
    started: Mapping[tuple[str, str], float],
    *,
    launch_is_working: bool,
    undriven_after: float = UNDRIVEN_AFTER_SECONDS,
    now: float | None = None,
) -> frozenset[tuple[str, str]]:
    """Which started nodes no live dispatch is driving — only where that is provable.

    The ledger says a node started and never settled; this says whether anything is
    actually running it. Saying so requires positive evidence that the registry is
    answering *for this run*, because two ordinary conditions make it answer nothing
    at all: a procfs it cannot read, and a reader whose ``TMPDIR`` is not the
    dispatcher's. Either would otherwise report every node in the graph as dead.

    So two proofs are demanded before any node is named. The registry must have seen
    at least one live dispatch, which is what shows this reader is looking at the
    scratch root the dispatchers write into. And the run's own launch must be
    observably working, which is what distinguishes *this* node having lost its
    dispatch from the whole run having stopped — the second is already reported one
    level up, by `orchestrator.liveness`, and reporting it again per node would bury
    the case this exists for.
    """
    if not dispatches or not launch_is_working:
        return frozenset()
    at = time.time() if now is None else now
    driven = {locator for dispatch in dispatches if (locator := dispatch.locator) is not None}
    return frozenset(
        locator
        for locator, began in started.items()
        if locator not in driven and at - began >= undriven_after
    )


def run_indicator(
    run_id: str, dispatches: Sequence[LiveDispatch] | None, *, now: float | None = None
) -> str | None:
    """One line naming what a run's live dispatches are doing, for a per-run row.

    ``None`` where nothing is known — an unreadable registry, or a scratch root that
    is not the dispatchers'. A row that says nothing keeps the picture it had; a row
    that says "no live dispatch" has to have been able to see one.
    """
    if dispatches is None:
        return None
    mine = [dispatch for dispatch in dispatches if dispatch.run_id == run_id]
    if not mine:
        # Same rule `undriven_locators` applies: claiming a run has nothing running
        # requires having seen that this reader can observe live dispatches at all.
        return None if not dispatches else "no live dispatch carries this run's ownership stamp"
    at = time.time() if now is None else now
    return f"{len(mine)} live dispatch(es): " + "; ".join(
        f"{dispatch.where()} {dispatch.turn.describe(now=at)}" for dispatch in mine
    )


def load_averages() -> tuple[float, float, float] | None:
    """This host's 1/5/15-minute load averages, or ``None`` where it keeps none."""
    try:
        return os.getloadavg()
    except (OSError, AttributeError):  # pragma: no cover - every Linux keeps them
        return None


def _describe_attribution(dispatches: Sequence[LiveDispatch], *, now: float) -> Iterator[str]:
    for dispatch in sorted(dispatches, key=lambda item: -item.runnable):
        if dispatch.runnable <= 0:
            continue
        yield (
            f"{dispatch.where()} {dispatch.turn.describe(now=now)} — "
            f"{dispatch.runnable} runnable process(es)"
        )


def load_indicator(
    dispatches: Sequence[LiveDispatch] | None, *, now: float | None = None
) -> str | None:
    """One header line: this host's load, and which dispatches are producing it.

    A planner reading a slow run has had to work backwards from a load average to the
    thing causing it by hand. Every gate, suite, and git walk a dispatched worker runs
    is in that worker's process tree and carries its ownership stamp, so the run and
    node responsible are named here rather than reconstructed later.
    """
    averages = load_averages()
    if averages is None:  # pragma: no cover - platform without load averages
        return None
    at = time.time() if now is None else now
    line = f"Load: {averages[0]:.2f} {averages[1]:.2f} {averages[2]:.2f} (1m 5m 15m)"
    if dispatches is None:
        return f"{line} — live dispatch attribution unavailable (procfs unreadable)"
    attributed = list(_describe_attribution(dispatches, now=at))
    if not attributed:
        return f"{line} — no live dispatch is contributing runnable work"
    return "\n".join([f"{line} — attributed to:", *(f"    {entry}" for entry in attributed)])
