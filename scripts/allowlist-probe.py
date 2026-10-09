"""`just probe-allowlist`: drive a real Claude Code and a real Codex through every row.

Reads the one invocation table, `tests/manager_invocations.py`, and keeps no copy of it.
It renders `config/manager-allowlist.toml` into a scratch project through `just
sync-allowlist`, then asks each tool, in a mode where a command no rule lets through is
refused rather than classified, to run every row it is held on — and prints, per row and
per tool, whether the row was let through or refused, against what the table says.

Nothing real is stopped, pushed, merged or signalled, by construction rather than by
hope. The scratch project carries its own `justfile`, so `just` resolves every recipe to
a stub before it could climb to a real one; `onevcs`, `gh`, `git` and `uv` are stubs
first on `PATH`, which Codex keeps because its scratch home turns the login shell off;
`cat`, `tail` and `grep` are recorders that then run the real reader; the one `kill`
targets a `sleep` this probe started; every other target is a path inside the scratch
tree. Each stub appends its own argv to a marker file outside the project only when it
actually runs, which is the observable: a row was let through when its argv reached the
marker, a write when its file changed, the `kill` when the sleep ended. A row that did not
get through is `refused` when Claude Code lists it in its `permission_denials`, `no effect`
when Claude Code asked to run it without refusing it, `not through` when Codex's own record
says it ran the command — a record that cannot say whether a rule or the command itself
stopped it — and `not attempted` when the tool never tried it, which satisfies a refused row
and fails an allowed one.

- **Claude Code** runs `claude -p` under `--permission-mode default`, which refuses a
  command no rule allows. The scratch project is untrusted, and Claude Code ignores an
  untrusted project's `permissions.allow`, so the rendered `.claude/settings.json` is
  handed over with `--settings` — the same file, never an edit to any identity's trust.
  Its `HOME` is a scratch home, so the `~` a read grant names is a worktree the probe made.
  Its shell runs `grep` as a function over its own bundled search tool, never the recorder
  first on `PATH`, so a `grep` row is read off the turn's own stream instead: let through
  when the turn asked to run that exact command and did not list it in `permission_denials`,
  which `--permission-mode default` fills with every command no rule allows.
- **Codex** runs `codex exec` under `--sandbox read-only` with a scratch `CODEX_HOME` that
  trusts the scratch project and holds a copy of an existing login; the user's own Codex
  home is only read. A command the synced rules allow runs outside the sandbox and reaches
  the marker; any other runs sandboxed, where the marker cannot be written.

A candidate refused for quota or auth falls through to the next identity, in the chain's
order, as `just smoke` does; a turn that ends without saying it attempted every step is
refused rather than read. Everything created is removed on the way out.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, NamedTuple, NoReturn

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

# The table is a test-support module, importable only once `tests/` is on the path above.
from manager_invocations import INVOCATIONS, Invocation, InvocationId, placeholders  # noqa: E402

SOURCE = REPO_ROOT / "config" / "manager-allowlist.toml"
TABLE = REPO_ROOT / "tests" / "manager_invocations.py"
JUSTFILE = REPO_ROOT / "justfile"
#: The agent chain: which identity is tried first is its order, never a copy of it.
CHAIN = REPO_ROOT / "oneharness.toml"
#: The judge side's config, whose cheaper Claude tier each Claude turn here runs on: what
#: is measured is the permission layer, not the model.
JUDGE = REPO_ROOT / "oneharness.judge.toml"
#: The pinned `oneharness` CLI, which resolves both configs for the probe: the one the lock
#: installs beside the interpreter running this, taken without resolving symlinks, as
#: `orchestrator/plan_store.py` takes its CLI, so a copy of this checkout run on another
#: checkout's environment reads that environment's install.
ONEHARNESS = Path(sys.executable).parent / "oneharness"
#: Phrases a refused turn says when the identity, not the probe, is the problem, matched as
#: whole words so an unrelated failure ("could not generate") is never read as a spent quota.
# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] Neither provider CLI publishes
# the wording of a quota or login refusal, so there is no source to reconcile with; the list only
# decides whether to try the next identity, and a refusal it misses stops the probe quoting the
# provider's own words rather than misreading any row.
EXHAUSTED = re.compile(
    r"\b(?:usage limit|rate limit|rate[- ]limited|hit your \w+ limit|quota|credit balance"
    r"|not logged in|log ?in|unauthori[sz]ed|authentication|401|403|429)\b",
    re.IGNORECASE,
)
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
#: The word every prompt asks a turn to end on once it has attempted every step.
FINISHED = "DONE"


def _finished(said: str) -> bool:
    """Whether a turn's last word is `FINISHED`.

    Its last word rather than the whole text, because a model that attempted every step
    often says so before the word it was asked to end on, on the same line or the one
    above; a turn that stopped short never ends on it.
    """
    words = re.findall(r"[A-Za-z]+", said)
    return bool(words) and words[-1] == FINISHED


#: Rows per Claude turn by default, so a long list does not outrun one turn's attention.
BATCH = 24
#: Programs replaced outright by a recorder, and readers recorded and then run for real.
STUBBED = ("onevcs", "gh", "git", "uv", "onejudge", "oneharness", "onemessagebus", "llmlint")
READERS = ("cat", "tail", "grep")
RECORDED = (*STUBBED, "just", *READERS)
#: Readers Claude Code's own shell replaces with a function that runs its bundled search
#: tool, so their recorder never sees Claude run them: Claude's own record of the command it
#: asked to run, absent from its `permission_denials`, says such a row got through there.
SHADOWED_BY_CLAUDE = ("grep",)
#: The shells Codex reports a command as run under, `<shell> -c <command>`.
SHELLS = ("bash", "sh", "zsh")
#: The Claude Code tools a row asks for, and so the only ones a refusal of a row can name.
# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The source of these tool names is
# Claude Code itself, which only a paid turn reaches: `just probe-allowlist`'s live run is their
# reconciliation, and a refusal naming any other tool is refused as unreadable rather than read.
DENIABLE = ("Bash", "Write", "Edit")
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
#: Shell builtins a row runs, each observed another way: `cd` changes nothing, what an
#: `echo` wrote is read from its target, and a `kill` from the sleep it targets.
OBSERVED_OTHERWISE = ("cd", "echo", "kill")
#: What an `env_from` may name: an environment variable.
VARIABLE = re.compile(r"[A-Z_][A-Z0-9_]*")
#: How a chain spells an identity: a harness, and a variant after a colon.
IDENTITY = re.compile(r"[a-z0-9][a-z0-9-]*(?::[a-z0-9][a-z0-9-]*)?")
#: This repository's scripts a row runs by path, each a recorder in the scratch project.
SCRIPTS = "./scripts/"
#: How a row id and a recipe are spelled, since each becomes a scratch path or a recipe line.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
#: How a script a row runs is spelled, since its recorder is written at that path.
SCRIPT = re.compile(r"\./scripts/[a-z0-9][a-z0-9-]*\.sh")
#: What a scratch path may be spelled with: every row is sent to a shell verbatim, so a
#: path needing quoting would change the command a tool is asked to run.
SAFE_PATH = re.compile(r"[A-Za-z0-9_./-]+")


def fail(message: str) -> NoReturn:
    print(f"probe-allowlist: {message}", file=sys.stderr)
    raise SystemExit(1)


class Identity(NamedTuple):
    """One identity of the chain, and the variable its `env_from` maps its home from —
    `None` for one that takes the harness's ambient home."""

    name: str
    variable: str | None


class CodexLogin(NamedTuple):
    """A Codex identity and the login home the probe copies for it."""

    identity: str
    home: Path


@functools.cache
def _resolved(config: Path) -> dict[str, Any]:
    """`config` as the pinned `oneharness config` resolves it, its `extends` chain included:
    the chain and each identity's model are read the way a turn resolves them, never by
    re-deriving oneharness's layering here."""
    try:
        shown = subprocess.run(
            [str(ONEHARNESS), "config", "--format", "json", "--config", str(config)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    # llmlint: ignore-block[changed_behavior_has_e2e] These refusals are reached only by a pinned
    # `oneharness` that is missing, hangs or rejects this checkout's own configs; the probe resolves
    # that fixed binary rather than anything on PATH, so no journey can double it, and session setup
    # and the sync drift gate already fail first on each of those states.
    except (OSError, subprocess.TimeoutExpired) as error:
        fail(
            f"the pinned oneharness could not resolve {config.name} ({error}); run `just "
            "bootstrap` to restore it, then retry"
        )
    if shown.returncode != 0:
        fail(
            f"`oneharness config` refused {config.name}: "
            f"{(shown.stdout + shown.stderr).strip()[:400]}; correct what it names, then retry"
        )
    try:
        resolved = json.loads(shown.stdout)
    except json.JSONDecodeError:
        resolved = None
    if not isinstance(resolved, dict):
        fail(
            f"`oneharness config --format json` answered {shown.stdout[:300]!r} for "
            f"{config.name}, not an object; run `just bootstrap` to restore the pinned CLI"
        )
    # llmlint: ignore-end[changed_behavior_has_e2e]
    return dict(resolved)


def _value(resolved: dict[str, Any], *keys: str) -> Any:
    """The resolved `value` of one field, or `None` where the config leaves it unset."""
    found: Any = resolved
    for key in keys:
        found = found.get(key) if isinstance(found, dict) else None
    return found.get("value") if isinstance(found, dict) else None


def _identities(harness: str) -> list[Identity]:
    """The chain's identities of one harness, in the chain's order."""
    # llmlint: ignore-block[changed_behavior_has_e2e] These refusals answer for this checkout's
    # tracked `oneharness.toml`, read through the pinned CLI; no journey can make it malformed
    # without changing it for every other test, and `tests/e2e/test_oneharness_timeout_e2e.py`
    # already holds its chain to the intended identities in order.
    resolved = _resolved(CHAIN)
    chain = _value(resolved, "harnesses")
    if not isinstance(chain, list) or not all(
        isinstance(entry, str) and IDENTITY.fullmatch(entry) for entry in chain
    ):
        fail(
            f"{CHAIN.name}'s `harnesses` is not a list of `harness[:variant]` identities: "
            f"{chain!r}; correct it, then retry"
        )
    home = {"claude-code": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}[harness]
    found = []
    for entry in chain or ():
        name, _, variant = entry.partition(":")
        if name != harness:
            continue
        variable = _value(resolved, "harness", harness, "variant", variant, "env_from", home)
        if variable is not None and not (
            isinstance(variable, str) and VARIABLE.fullmatch(variable)
        ):
            fail(f"{entry}'s `env_from.{home}` is {variable!r}, not a variable name; correct it")
        found.append(Identity(entry, variable))
    if not found:
        fail(f"{CHAIN.name}'s `harnesses` names no {harness} identity; name one, then retry")
    # llmlint: ignore-end[changed_behavior_has_e2e]
    return found


def _model(config: Path, harness: str, variant: str) -> str:
    """The model `config` resolves for one identity: its variant's, else its harness's."""
    resolved = _resolved(config)
    model = _value(resolved, "harness", harness, "variant", variant, "model") or _value(
        resolved, "harness", harness, "model"
    )
    # llmlint: ignore-block[changed_behavior_has_e2e] Answers for a tracked config every
    # identity of which pins a model, which `tests/e2e/test_oneharness_timeout_e2e.py` reads
    # identity by identity; no journey can unpin one without changing it for every other test.
    if not isinstance(model, str) or not model:
        fail(f"{config.name} names no model for {harness}:{variant}; name one, then retry")
    # llmlint: ignore-end[changed_behavior_has_e2e]
    return str(model)


@dataclass
class Scratch:
    """The probe's scratch tree, every path in it the probe's own."""

    root: Path
    project: Path = field(init=False)
    bin: Path = field(init=False)
    #: The home Claude Code's `~` is bound to, so a `Read(~/...)` rule names a scratch
    #: path rather than the host's own worktrees.
    home: Path = field(init=False)
    workspace: Path = field(init=False)
    marks: Path = field(init=False)

    def __post_init__(self) -> None:
        self.project = self.root / "project"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        self.workspace = (
            self.home / ".onevcs" / "workspaces" / "github.com-example" / "runs" / "s-probe"
        ) / "worktree"
        self.marks = self.root / "marks.log"


@dataclass
class Row:
    """One table row as the probe runs it: its command filled in."""

    invocation: Invocation
    command: str
    #: The argv a recorder writes when this row actually ran, or `None` where none runs.
    marker: str | None
    #: For a row that writes, the file it is asked to write.
    path: Path | None = None
    #: The original content of that file, which a refused row must leave alone.
    original: str | None = None


def _values(scratch: Scratch, invocation: Invocation, pid: int) -> dict[str, str]:
    """Placeholder values unique to one row, so each row's argv is its own; the envelope is
    the run's own file under the one directory its grant names."""
    tag = invocation.id
    runs = scratch.project / "runs"
    return {
        "run": f"run-{tag}",
        "node": "node-1",
        "log": str(scratch.project / ".logs" / f"watch-{tag}.log"),
        "cursor": f"cursor-{tag}",
        "co": f"checkout-{tag}",
        "branch": f"probe/{tag}",
        "base": "main",
        "envelope": str(scratch.project / "scratch" / "envelopes" / f"run-{tag}.json"),
        "correlation": f"c-{tag}",
        "brief": str(scratch.project / "scratch" / f"brief-{tag}.md"),
        "project": f"plans:probe-{tag}",
        "reference": f"github.com/example/probe-{tag}",
        "target": "pypi",
        "version": "0.0.1",
        "session": f"s-{tag}",
        "feedback": str(scratch.project / "scratch" / f"feedback-{tag}.md"),
        "workspace": str(scratch.workspace),
        "runs": str(runs),
        "label": f"stage-{tag}",
        "checkout": str(scratch.project),
        "owner": "example-probe",
        "name": f"nonexistent-{tag}",
        "file": str(scratch.project / "scratch" / "protection.json"),
        "ci_run": "1",
        "pid": str(pid),
    }


def _marker(command: str) -> str | None:
    """What a recorder writes when the last command of this line runs, if one runs it."""
    last = command.replace(" && ", " ; ").replace("; ", " ; ").split(" ; ")[-1].strip()
    words = shlex.split(last)
    if not words or ">" in words:
        return None
    if words[0] not in RECORDED and not words[0].startswith(SCRIPTS):
        return None
    return shlex.join(words)


def _recorder(path: Path, marks: Path, name: str, then: str | None = None) -> None:
    """A program that appends its argv to `marks`, then runs `then` or nothing."""
    tail = f'exec {shlex.quote(then)} "$@"\n' if then else f"echo 'probe stub: {name} ran'\n"
    path.write_text(
        "#!/bin/sh\n"
        f"# probe-allowlist recorder for {name}.\n"
        f'python3 -c \'import shlex,sys; print(shlex.join(sys.argv[1:]))\' "{name}" "$@" '
        f">> {shlex.quote(str(marks))}\n" + tail,
        encoding="utf-8",
    )
    path.chmod(0o755)


def _scripts() -> set[str]:
    """Every script of this repository a row runs by path."""
    return {
        invocation.command.split()[0]
        for invocation in INVOCATIONS
        if invocation.command.startswith(SCRIPTS)
    }


#: What a row's command line may be joined by: each part starts with the program it runs.
#: A newline ends a command as `;` does, so a part after one is held to a recorder too.
SEQUENCE = re.compile(r"&&|;|\n")
#: Shell forms that run a program no part's first word names — a pipe, an `||`, a
#: substitution, a subshell, a backgrounded job — which no row may use.
HIDDEN = re.compile(r"\||\$\(|`|<\(|\(|\)|&(?!&)")


def unrecorded(invocations: tuple[Invocation, ...] = INVOCATIONS) -> list[str]:
    """Every program a row runs that nothing here records, which a row must never reach:
    it would run the real one, and nothing could say whether it had. A row running one
    through a pipe, a substitution or a subshell is named by its id, since its programs
    cannot be read off its parts."""
    shell = [invocation for invocation in invocations if invocation.kind == "shell"]
    hidden = {
        f"{invocation.id} (a pipe, substitution or subshell)"
        for invocation in shell
        if HIDDEN.search(invocation.command.replace("&&", ""))
    }
    programs = {
        segment.split()[0]
        for invocation in shell
        for segment in SEQUENCE.split(invocation.command)
        if segment.split()
    }
    return sorted(
        hidden
        | {
            program
            for program in programs
            if program not in (*RECORDED, *OBSERVED_OTHERWISE) and not program.startswith(SCRIPTS)
        }
    )


def _written(scratch: Scratch, invocation: Invocation, command: str) -> Path | None:
    """The file a row writes — a write row's path, or a redirect's target — held inside the
    scratch tree, because the probe reads it and puts its content back afterwards."""
    written = Path(command) if invocation.kind == "write" else None
    if " > " in command:
        written = Path(command.rsplit("> ", 1)[1])
    if written is not None and not written.resolve().is_relative_to(scratch.root.resolve()):
        fail(
            f"row {invocation.id} writes {written}, outside the probe's scratch tree "
            f"{scratch.root}; spell its path with a placeholder the probe fills under it, "
            "then retry"
        )
    return written


def _recipes() -> set[str]:
    """Every recipe a row names, so the scratch justfile stubs each one."""
    found = set()
    for invocation in INVOCATIONS:
        for segment in invocation.command.replace("&&", ";").split(";"):
            words = segment.split()
            if len(words) > 1 and words[0] == "just":
                found.add(words[1])
    return found


def prepare(scratch: Scratch, pid: int) -> list[Row]:
    """Lay out the scratch tree, render the allowlist into it, and fill every row."""
    if unrecorded():
        fail(
            f"rows run {unrecorded()}, which the probe records nothing for; add each to "
            "STUBBED or READERS in scripts/allowlist-probe.py, then retry"
        )
    # llmlint: ignore-block[changed_behavior_has_e2e] These guards answer for the tracked invocation
    # table, which a journey cannot edit without changing the table every other test reads;
    # `tests/test_agent_allowlist.py` refuses a row naming a placeholder outside the table's
    # declared set.
    unsafe = sorted(
        {invocation.id for invocation in INVOCATIONS if not NAME.fullmatch(invocation.id)}
        | {script for script in _scripts() if not SCRIPT.fullmatch(script)}
        | {recipe for recipe in _recipes() if not NAME.fullmatch(recipe)}
    )
    if unsafe:
        fail(
            f"the table names {unsafe}, which the probe cannot lay out as a scratch path or a "
            "recipe; spell each as letters, digits and hyphens, a script as "
            "`./scripts/<name>.sh`, then retry"
        )
    unknown = {name for invocation in INVOCATIONS for name in placeholders(invocation)} - set(
        _values(scratch, INVOCATIONS[0], pid)
    )
    if unknown:
        fail(
            f"the table names placeholders the probe does not fill: {sorted(unknown)}; give each "
            "a value in `_values` in scripts/allowlist-probe.py, or spell the row with one it "
            "fills, then retry"
        )
    # llmlint: ignore-end[changed_behavior_has_e2e]
    for directory in (scratch.project / "scratch", scratch.project / ".logs", scratch.bin):
        directory.mkdir(parents=True)
    (scratch.workspace / ".logs").mkdir(parents=True)
    initialised = subprocess.run(
        ["git", "init", "--quiet", str(scratch.project)], capture_output=True, text=True
    )
    if initialised.returncode != 0:
        fail(
            f"git could not initialise the scratch project ({initialised.stderr.strip()}); "
            "check that git runs and the scratch directory is writable, then retry"
        )
    scratch.marks.touch()
    for name in STUBBED:
        _recorder(scratch.bin / name, scratch.marks, name)
    for name in READERS:
        real = shutil.which(name)
        # llmlint: ignore-block[changed_behavior_has_e2e] The readers are coreutils and grep, which
        # the recipe's own shell and `uv` need before the probe starts, so no journey can take one
        # off PATH and still reach this line.
        if real is None:
            fail(f"{name} is not on PATH; install it, then retry")
        # llmlint: ignore-end[changed_behavior_has_e2e]
        _recorder(scratch.bin / name, scratch.marks, name, then=real)
    for script in _scripts():
        (scratch.project / script).parent.mkdir(parents=True, exist_ok=True)
        _recorder(scratch.project / script, scratch.marks, script)
    recorder = scratch.bin / ".record-just"
    _recorder(recorder, scratch.marks, "just")
    justfile = ["set positional-arguments", ""]
    for recipe in sorted(_recipes()):
        justfile += [f"{recipe} *args:", f'    @{shlex.quote(str(recorder))} {recipe} "$@"', ""]
    (scratch.project / "justfile").write_text("\n".join(justfile), encoding="utf-8")
    (scratch.project / "scratch" / "protection.json").write_text("{}\n", encoding="utf-8")

    rendered = subprocess.run(
        ["just", "--justfile", str(JUSTFILE), "sync-allowlist", str(scratch.project)],
        capture_output=True,
        text=True,
        check=False,
    )
    if rendered.returncode != 0:
        fail(
            "`just sync-allowlist` refused to render the source into the scratch project; "
            f"correct what it names, then retry:\n{rendered.stdout}{rendered.stderr}"
        )

    rows = []
    for invocation in INVOCATIONS:
        values = _values(scratch, invocation, pid)
        command = invocation.command.format(**values)
        run_dir = scratch.project / "runs" / values["run"]
        (run_dir / "channel").mkdir(parents=True, exist_ok=True)
        (run_dir / "launch.json").write_text('{"probe": "launch"}\n', encoding="utf-8")
        (run_dir / "channel" / "commands-cursor.json").write_text(
            '{"probe": 0}\n', encoding="utf-8"
        )
        (run_dir / "driver.log").write_text("probe driver log\n", encoding="utf-8")
        for logs in (scratch.workspace / ".logs", scratch.project / ".logs"):
            (logs / f"{values['label']}.log").write_text("probe error line\n", encoding="utf-8")
        written = _written(scratch, invocation, command)
        rows.append(
            Row(
                invocation,
                command,
                None if invocation.kind == "write" else _marker(command),
                written,
                written.read_text(encoding="utf-8")
                if written is not None and written.exists()
                else None,
            )
        )
    # llmlint: ignore-block[changed_behavior_has_e2e] This guard answers for the tracked invocation
    # table, which a journey cannot edit without changing the table every other test reads.
    markers = [row.marker for row in rows if row.marker is not None]
    if len(markers) != len(set(markers)):
        fail(
            "two rows record the same argv, so the marker cannot tell them apart; "
            "give each a placeholder"
        )
    # llmlint: ignore-end[changed_behavior_has_e2e]
    return rows


def _instruction(row: Row, number: int) -> str:
    if row.invocation.kind == "write":
        return (
            f"{number}. Use your file-write tool (not the shell) to write the file "
            f"{row.command} with the content {{}}"
        )
    return f"{number}. {row.command}"


def _prompt(rows: list[Row]) -> str:
    listing = "\n".join(_instruction(row, number) for number, row in enumerate(rows, 1))
    return (
        "You are a non-interactive test fixture in a scratch directory, probing which "
        "commands a permission policy lets through. Every program here is a harmless stub. "
        "Carry out each numbered step below, in order, one tool call per step, exactly as "
        "written: run each shell command with your shell tool verbatim — do not alter, "
        "quote differently, combine, split or skip it. If a step is refused or fails, do not "
        "retry it, do not ask for approval or escalation, and do not achieve it any other "
        "way; go on to the next step. Use no other tool and run no other command. When every "
        f"step has been attempted, reply with the single word {FINISHED}.\n\n" + listing
    )


class Outcome(Enum):
    """What one tool did with one row, as the probe observed it."""

    #: It got through: its marker, its file or the sleep say so, or the tool's own record
    #: of running a command whose recorder its shell shadows.
    THROUGH = "let through"
    #: Claude Code listed it in its `permission_denials`.
    REFUSED = "refused"
    #: Codex's record says it ran the command, and nothing it would have done happened — a
    #: record that cannot say whether a rule or the command itself stopped it.
    NOT_THROUGH = "not through"
    #: Claude Code asked to run it and did not refuse it, and nothing it would have done
    #: happened: permitted, then stopped by something other than a rule.
    NO_EFFECT = "no effect"
    #: The tool never tried it.
    NOT_ATTEMPTED = "not attempted"


def _let_through(
    row: Row,
    ran: set[str],
    stopped: set[str],
    alive: Callable[[], bool],
    stopped_as: Outcome,
    vouched: frozenset[str] = frozenset(),
    requested: frozenset[str] = frozenset(),
) -> Outcome:
    """What the row came to: through, by what it did or, for a command in `vouched`, by the
    tool's own record of running it; `stopped_as` when it is in `stopped`; no effect when the
    tool asked to run it (`requested`) without refusing it; else never attempted."""
    if row.invocation.id == "kill":
        through = not alive()
    elif row.path is not None:
        now = row.path.read_text(encoding="utf-8") if row.path.exists() else None
        through = now != row.original
    else:
        through = (row.marker is not None and row.marker in ran) or row.command in vouched
    if through:
        return Outcome.THROUGH
    if row.command in stopped:
        return stopped_as
    return Outcome.NO_EFFECT if row.command in requested else Outcome.NOT_ATTEMPTED


def _turn(
    argv: list[str], scratch: Scratch, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """One real turn in the scratch project, or a refusal naming what to do."""
    try:
        return subprocess.run(
            argv,
            cwd=scratch.project,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=1800,
            env=env,
        )
    # llmlint: ignore-block[changed_behavior_has_e2e] Reaching this refusal means a turn running for
    # thirty minutes, which no journey can spend; the bound is a constant and the refusal only names
    # it.
    except subprocess.TimeoutExpired:
        fail(
            f"{argv[0]} ran past 30 minutes on one turn; re-run with a smaller `--batch` or "
            "narrow it with `--row`"
        )
    # llmlint: ignore-end[changed_behavior_has_e2e]
    except OSError as error:
        fail(f"{argv[0]} could not be started ({error}); install it or fix PATH, then retry")
    raise AssertionError


def _environment(scratch: Scratch) -> dict[str, str]:
    # llmlint: ignore-block[boundary_inputs_validated] The operator's own environment is what each
    # tool is meant to run in — its logins and the PATH to the real tools; nothing in it comes from
    # the table or a provider, and the probe only puts its recorders first on PATH.
    return {**os.environ, "PATH": f"{scratch.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    # llmlint: ignore-end[boundary_inputs_validated]


class ClaudeEvents(NamedTuple):
    """What a `claude -p` event stream says, before it is held to a turn's ending."""

    #: The turn's closing result event, or `None` for a stream carrying none.
    result: dict[str, Any] | None
    #: Every command or path the turn asked its shell or write tool to act on.
    requested: frozenset[str]


class ClaudeRun(NamedTuple):
    """Every Claude Code batch, as run under the identity that answered."""

    identity: str
    #: Every step a turn reported refusing.
    refused: set[str]
    #: Every command of a program its shell shadows that a turn ran without refusing.
    vouched: frozenset[str]
    #: Every command or path a turn asked to act on, refused or not.
    requested: frozenset[str]


class CodexTurn(NamedTuple):
    """What a `codex exec --json` event stream says."""

    #: Every shell command it executed, as the command line it ran under `bash -c`.
    executed: set[str]
    #: Its last message, empty unless the stream says the turn ended.
    last: str


class CodexRun(NamedTuple):
    """The Codex rows, as run under the login that answered."""

    identity: str
    #: Every command it attempted.
    executed: set[str]


class ClaudeTurn(NamedTuple):
    """One `claude -p --output-format json` turn, as the probe reads it."""

    #: Every step the turn reported refusing: its command, or the path a write named.
    refused: frozenset[str]
    #: Why the turn cannot be read, or `None` when it finished every step.
    problem: str | None = None
    #: Whether that problem is the identity's — its quota or its login — not the turn's.
    exhausted: bool = False
    #: Every command the turn asked its shell tool to run, refused or not.
    requested: frozenset[str] = frozenset()


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The source of these event shapes
# is the provider CLI itself, which only a paid turn reaches, so no deterministic tier can hold
# them: the reconciliation is `just probe-allowlist`'s own live run, which refuses every event it
# cannot read with a next step rather than reading it, and the journeys hold that refusal with
# doubles.
def _claude_events(stdout: str) -> ClaudeEvents:
    """From `claude -p --output-format stream-json`'s events: its closing result, and every
    command its shell tool was asked to run. `None` for a stream carrying no result; a second
    result, and any turn's own event after the first, is refused, since the first result is
    what closes a turn and a later one would replace its refusals, while the `system` notices
    Claude Code writes after it say nothing about the turn."""
    report: dict[str, Any] | None = None
    requested: set[str] = set()
    for line in filter(str.strip, stdout.splitlines()):
        event = json.loads(line)
        if not isinstance(event, dict):
            raise json.JSONDecodeError("an event that is not an object", line, 0)
        if not isinstance(event.get("type"), str):
            raise ValueError(f"an event with no type: {line[:300]!r}")
        if report is not None and event.get("type") == "result":
            raise ValueError(f"a second result after the turn's first: {line[:300]!r}")
        if report is not None and event.get("type") != "system":
            raise ValueError(f"an event after the turn's result: {line[:300]!r}")
        if event.get("type") == "result":
            report = event
        if event["type"] != "assistant":
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            raise ValueError(f"an assistant event with no content list: {line[:300]!r}")
        for block in content:
            if not isinstance(block, dict):
                raise ValueError(f"a content block that is not an object: {line[:300]!r}")
            # llmlint: ignore-block[boundary_inputs_validated] Other tools (a read, a listing) are
            # the model's own way to a step; the probe's evidence is only what it records and what
            # Bash or a write attempted, and a row no such tool attempted reads `not attempted`,
            # never let through.
            tool_input = block.get("input")
            if block.get("type") != "tool_use" or not isinstance(tool_input, dict):
                continue
            key = "command" if block.get("name") == "Bash" else "file_path"
            if block.get("name") in DENIABLE and isinstance(tool_input.get(key), str):
                requested.add(tool_input[key])
            # llmlint: ignore-end[boundary_inputs_validated]
    return ClaudeEvents(report, frozenset(requested))


def _claude_turn(turn: subprocess.CompletedProcess[str]) -> ClaudeTurn:
    """A turn held to the fields and the ending the probe relies on."""
    try:
        report, requested = _claude_events(turn.stdout)
    except json.JSONDecodeError:
        return ClaudeTurn(frozenset(), f"not JSON: {(turn.stdout + turn.stderr)[:400]}")
    except ValueError as error:
        return ClaudeTurn(frozenset(), f"not a claude-code stream: {error}")
    if report is None or not isinstance(report.get("is_error"), bool):
        return ClaudeTurn(
            frozenset(), f"not a claude-code result: {(turn.stdout + turn.stderr)[:400]!r}"
        )
    said = report.get("result")
    if not isinstance(said, str):
        return ClaudeTurn(frozenset(), f"a result that is not text: {said!r}"[:400])
    if report["is_error"]:
        exhausted = EXHAUSTED.search(said) is not None
        return ClaudeTurn(frozenset(), said[:400], exhausted)
    denials = report.get("permission_denials")
    if not isinstance(denials, list) or not all(
        isinstance(denial, dict)
        and denial.get("tool_name") in DENIABLE
        and isinstance(denial.get("tool_input"), dict)
        and isinstance(
            denial["tool_input"].get("command") or denial["tool_input"].get("file_path"), str
        )
        for denial in denials
    ):
        return ClaudeTurn(frozenset(), f"unreadable permission_denials: {denials!r}"[:400])
    denied = frozenset(
        str(denial["tool_input"].get("command") or denial["tool_input"].get("file_path"))
        for denial in denials
    )
    if denied - requested:
        # A denial is evidence a row was refused only for something the turn asked to run.
        return ClaudeTurn(
            frozenset(), f"denials for what the turn never requested: {sorted(denied - requested)}"
        )
    if turn.returncode != 0 or not _finished(said):
        return ClaudeTurn(
            frozenset(), f"it exited {turn.returncode} saying {said[:300]!r}, not {FINISHED}"
        )
    return ClaudeTurn(denied, requested=requested)


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
def _fresh(scratch: Scratch, rows: list[Row]) -> None:
    """Put back what an earlier attempt could have changed, so each identity is read alone."""
    scratch.marks.write_text("", encoding="utf-8")
    for row in rows:
        if row.path is None:
            continue
        if row.original is None:
            row.path.unlink(missing_ok=True)
        else:
            row.path.write_text(row.original, encoding="utf-8")


def _unspent(sleeper_alive: Callable[[], bool], identity: str) -> None:
    """Refuse to hand the rows to another identity once this one's turn ended the sleep: the
    kill row's target is gone, so nothing the next identity does to it could be read."""
    if not sleeper_alive():
        fail(
            f"{identity} ran out after its turn ended the probe's own sleep, so the kill row "
            "cannot be read under the next identity; re-run `just probe-allowlist` once "
            f"{identity} is available again, or narrow it with `--row` leaving out `kill`"
        )


def drive_claude(
    scratch: Scratch, rows: list[Row], batch: int, sleeper_alive: Callable[[], bool]
) -> ClaudeRun:
    """Run every batch under the first identity that answers: its name, every step it
    reported refusing, and every command of a program its shell shadows that it asked to run
    and did not report refusing."""
    settings = scratch.project / ".claude" / "settings.json"
    tried = []
    for identity in _identities("claude-code"):
        config_dir = os.environ.get(identity.variable) if identity.variable else None
        if not config_dir:
            tried.append(f"{identity.name}: no {identity.variable or 'CLAUDE_CONFIG_DIR'} set")
            continue
        if not (Path(config_dir).is_absolute() and Path(config_dir).is_dir()):
            tried.append(f"{identity.name}: {config_dir!r} is not an absolute directory")
            continue
        model = _model(JUDGE, "claude-code", identity.name.partition(":")[2])
        exhausted = False
        refused: set[str] = set()
        requested: set[str] = set()
        _fresh(scratch, rows)
        for start in range(0, len(rows), batch):
            # llmlint: ignore-block[changed_behavior_has_e2e] What a real `claude -p
            # --permission-mode default` lets through is observable only by spending a paid Claude
            # Code turn, which `just probe-allowlist` does and every test tier deliberately does
            # not; the live probe's output over the finished tree is that proof, and the journeys
            # drive everything around the turn with a double.
            turn = _turn(
                [
                    "claude",
                    "-p",
                    _prompt(rows[start : start + batch]),
                    "--settings",
                    str(settings),
                    "--permission-mode",
                    "default",
                    "--output-format",
                    "stream-json",
                    "--verbose",
                    "--model",
                    model,
                ],
                scratch,
                {
                    **_environment(scratch),
                    "CLAUDE_CONFIG_DIR": config_dir,
                    "HOME": str(scratch.home),
                },
            )
            # llmlint: ignore-end[changed_behavior_has_e2e]
            read = _claude_turn(turn)
            if read.exhausted:
                tried.append(f"{identity.name}: {str(read.problem)[:160]}")
                exhausted = True
                _unspent(sleeper_alive, identity.name)
                break
            if read.problem is not None:
                fail(
                    f"claude-code as {identity.name} did not finish the turn: {read.problem}; "
                    "re-run `just probe-allowlist --tool claude-code`, and if it repeats, "
                    "narrow it with `--row` to the steps it stopped at"
                )
            refused |= read.refused
            requested |= read.requested
        if not exhausted:
            vouched = frozenset(
                command
                for command in requested - refused
                if any(
                    part.split(maxsplit=1)[:1] == [program]
                    for part in re.split(r" && |; ", command)
                    for program in SHADOWED_BY_CLAUDE
                )
            )
            return ClaudeRun(identity.name, refused, vouched, frozenset(requested))
    fail(
        "no claude-code identity could take a turn ("
        + "; ".join(tried)
        + "); log one in or wait out its limit, then retry with `--tool codex` meanwhile"
    )
    raise AssertionError


def _codex_logins() -> list[CodexLogin]:
    """Each Codex identity's login home, in the chain's order: the variable its `env_from`
    names, or the ambient `CODEX_HOME` — `~/.codex` by default — for one naming none."""
    logins = []
    for identity in _identities("codex"):
        variable = identity.variable or "CODEX_HOME"
        value = os.environ.get(variable)
        logins.append(CodexLogin(identity.name, Path(value) if value else Path.home() / ".codex"))
    return logins


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The source of these event shapes
# is the provider CLI itself, which only a paid turn reaches, so no deterministic tier can hold
# them: the reconciliation is `just probe-allowlist`'s own live run, which refuses every event it
# cannot read with a next step rather than reading it, and the journeys hold that refusal with
# doubles.
def _codex_event(line: str) -> dict[str, Any]:
    """One line of `codex exec --json`, held to a typed event, or a refusal naming it."""
    try:
        event: object = json.loads(line)
    except json.JSONDecodeError:
        fail(
            f"`codex exec --json` wrote a line that is not an event: {line[:300]!r}; check "
            "that the installed `codex` still writes JSONL events, then retry"
        )
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        fail(
            f"`codex exec --json` wrote an event with no type: {line[:300]!r}; check that "
            "the installed `codex` still writes typed JSONL events, then retry"
        )
    return event


def _codex_refusal(turn: subprocess.CompletedProcess[str]) -> str:
    """What a failed `codex exec --json` said about itself: its stderr and the message of
    each `error` or `turn.failed` event — never a command's output, which an event's item
    carries and which may say anything. A line that is not a typed event is refused here,
    before it could let a limit its stderr names pass the turn to the next login."""
    said = [turn.stderr]
    for line in filter(str.strip, turn.stdout.splitlines()):
        event = _codex_event(line)
        error = event.get("error")
        message = {
            "error": event.get("message"),
            "turn.failed": error.get("message") if isinstance(error, dict) else None,
        }.get(str(event.get("type")))
        if isinstance(message, str):
            said.append(message)
    return "\n".join(said)


def _codex_turn(stdout: str) -> CodexTurn:
    """From `codex exec --json`'s events: every shell command it executed, as the command
    line it ran under `bash -c`, and its last message — empty unless the stream carries the
    `turn.completed` event that says the turn ended rather than stopped."""
    executed: set[str] = set()
    last = ""
    completed = False
    for line in filter(str.strip, stdout.splitlines()):
        event = _codex_event(line)
        item = event.get("item")
        if item is not None and not event["type"].startswith("item."):
            fail(
                f"`codex exec --json` wrote an item on a `{event['type']}` event: {line[:300]!r}; "
                "check that the installed `codex` still reports items on `item.*` events, then "
                "retry"
            )
        if completed and item is not None:
            fail(
                f"`codex exec --json` wrote an item after `turn.completed`: {line[:300]!r}; "
                "re-run `just probe-allowlist --tool codex`, and if it repeats, check the "
                "installed `codex` still closes a turn with that event"
            )
        if completed and event["type"] == "turn.completed":
            fail(
                f"`codex exec --json` wrote a second `turn.completed`: {line[:300]!r}; re-run "
                "`just probe-allowlist --tool codex`, and if it repeats, check the installed "
                "`codex` still closes a turn with one such event"
            )
        if event["type"] == "turn.failed":
            fail(
                f"codex reported its turn failed: {line[:300]!r}; re-run `just probe-allowlist "
                "--tool codex` once what it names is fixed"
            )
        completed = completed or event["type"] == "turn.completed"
        if item is None:
            continue
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            fail(
                f"`codex exec --json` wrote an item with no type: {line[:300]!r}; check that "
                "the installed `codex` still writes typed items, then retry"
            )
        # Only a finished item counts: a message still being written, or a command still
        # running, is not yet what the turn said or attempted.
        if event["type"] != "item.completed":
            continue
        if item["type"] == "agent_message" and isinstance(item.get("text"), str):
            last = item["text"]
        command = item.get("command")
        if item["type"] == "command_execution":
            if not isinstance(command, str):
                fail(
                    f"`codex exec --json` reported an execution with no command: {line[:300]!r}; "
                    "check that the installed `codex` still names the command it ran, then retry"
                )
            try:
                words = shlex.split(command)
            except ValueError as error:
                fail(
                    f"codex reported a command no shell could parse ({error}): {command[:300]!r}; "
                    "re-run `just probe-allowlist --tool codex`"
                )
            if len(words) == 3 and words[1] == "-c":
                if Path(words[0]).name not in SHELLS:
                    fail(
                        f"codex ran a command under {words[0]!r}, not a shell the probe reads "
                        f"({', '.join(SHELLS)}): {command[:300]!r}; add it to SHELLS in "
                        "scripts/allowlist-probe.py if it is one, then retry"
                    )
                executed.add(words[2])
            else:
                # llmlint: ignore-block[boundary_inputs_validated] Codex may report an argv it ran
                # with no shell wrapper, which is then the attempt itself; an attempt only ever
                # makes a row read refused or not attempted beside its marker, never let through.
                executed.add(command)
                # llmlint: ignore-end[boundary_inputs_validated]
    return CodexTurn(executed, last if completed else "")


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]
def drive_codex(scratch: Scratch, rows: list[Row], sleeper_alive: Callable[[], bool]) -> CodexRun:
    """Run the Codex rows under the first login that answers: its name, and every command
    it attempted."""
    home = scratch.root / "codex-home"
    real = scratch.project.resolve()
    tried = []
    for login in _codex_logins():
        if not (login.home.is_absolute() and (login.home / "auth.json").is_file()):
            tried.append(f"{login.identity}: no login at {login.home}")
            continue
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir()
        _fresh(scratch, rows)
        shutil.copy(login.home / "auth.json", home / "auth.json")
        model = _model(CHAIN, "codex", login.identity.partition(":")[2])
        (home / "config.toml").write_text(
            f"model = {json.dumps(model)}\nallow_login_shell = false\n\n"
            + "".join(
                f'[projects.{json.dumps(str(path))}]\ntrust_level = "trusted"\n\n'
                for path in sorted({scratch.project, real})
            ),
            encoding="utf-8",
        )
        # llmlint: ignore-block[changed_behavior_has_e2e] What a real `codex exec --sandbox
        # read-only` does with a command its synced rules allow is observable only by spending a
        # paid Codex turn, which `just probe-allowlist` does and every test tier deliberately does
        # not; the live probe's output over the finished tree is that proof, and the journeys drive
        # everything around the turn with a double.
        turn = _turn(
            [
                "codex",
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                _prompt(rows),
            ],
            scratch,
            {**_environment(scratch), "CODEX_HOME": str(home)},
        )
        # llmlint: ignore-end[changed_behavior_has_e2e]
        said = turn.stdout + turn.stderr
        if turn.returncode != 0 and EXHAUSTED.search(_codex_refusal(turn)):
            tried.append(f"{login.identity}: exited {turn.returncode}")
            _unspent(sleeper_alive, login.identity)
            continue
        executed, last = _codex_turn(turn.stdout)
        if turn.returncode != 0 or not _finished(last):
            fail(
                f"codex as {login.identity} exited {turn.returncode} without {FINISHED}; its "
                "output is below — re-run `just probe-allowlist --tool codex`, and if it "
                f"repeats, narrow it with `--row`:\n{said[-3000:]}"
            )
        return CodexRun(login.identity, executed)
    fail(
        "no codex identity could take a turn ("
        + "; ".join(tried)
        + "); `codex login` one or wait out its limit, "
        "then retry with `--tool claude-code` meanwhile"
    )
    raise AssertionError


def _consistent_with_the_table(tool: str, row: Row, got: Outcome) -> bool:
    """Whether what a tool did with a row is consistent with the table. An allowed row must
    get through. Any other must be seen refused under Claude Code, whose `permission_denials`
    records every refusal; under Codex, which keeps no record of a command it declined to
    run, it must only not get through, so an unattempted one is consistent and not proof."""
    if row.invocation.verdict == "allow":
        return got is Outcome.THROUGH
    if tool == "claude-code":
        return got is Outcome.REFUSED
    return got in (Outcome.NOT_THROUGH, Outcome.NOT_ATTEMPTED)


# llmlint: ignore-block[tool_output_is_signal] One line per row and tool, under a heading
# naming the identity that answered, is this command's product rather than progress: the
# operator runs it to read, row by row, what each tool let through, and the closing line
# says whether any row disagrees with the table.
def report(
    tool: str, identity: str, rows: list[Row], outcome: Mapping[InvocationId, Outcome]
) -> int:
    """Print one line per row for one tool; the number that disagreed with the table."""
    print(f"{tool} (as {identity}):")
    wrong = 0
    for row in rows:
        invocation = row.invocation
        if tool not in invocation.tools:
            print(f"  {tool:<11} {invocation.id:<28} unexpressible: {invocation.why_one_tool}")
            continue
        got = outcome.get(invocation.id, Outcome.NOT_ATTEMPTED)
        word = got.value
        verdict = "ok" if _consistent_with_the_table(tool, row, got) else "MISMATCH"
        wrong += verdict != "ok"
        print(
            f"  {tool:<11} {invocation.id:<28} {invocation.verdict:<7} {word:<12} "
            f"{verdict}  {row.command}"
        )
    return wrong


def summarize(rows: int, wrong: int) -> None:
    held = f"{wrong} row(s) disagree with the table" if wrong else "every row as the table says"
    print(
        f"probe-allowlist: {rows} rows of {TABLE.relative_to(REPO_ROOT)} against "
        f"{SOURCE.relative_to(REPO_ROOT)}: {held}"
    )


# llmlint: ignore-end[tool_output_is_signal]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--tool",
        action="append",
        choices=("claude-code", "codex"),
        help="drive only this tool (repeatable; default both)",
    )
    parser.add_argument(
        "--row",
        action="append",
        choices=[invocation.id for invocation in INVOCATIONS],
        metavar="ID",
        help="probe only this row of tests/manager_invocations.py (repeatable)",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=BATCH,
        metavar="N",
        help=f"rows per Claude Code turn (default {BATCH}); fewer for a model that loses steps",
    )
    arguments = parser.parse_args()
    if arguments.batch < 1:
        parser.error("--batch takes a positive number of rows")
    tools = list(dict.fromkeys(arguments.tool or ["claude-code", "codex"]))
    for program in (
        "claude" if "claude-code" in tools else None,
        "codex" if "codex" in tools else None,
        "just",
        "sleep",
    ):
        if program and shutil.which(program) is None:
            fail(f"{program} is not on PATH; install it, then retry")
    base = os.environ.get("ONEPIPELINE_NODE_SCRATCH_DIR") or tempfile.gettempdir()
    if (
        not SAFE_PATH.fullmatch(base)
        or not os.path.isabs(base)
        or ".." in Path(base).parts
        or not os.path.isdir(base)
    ):
        fail(
            f"the scratch directory {base!r} is not an existing absolute directory spelled "
            "without `..` or shell quoting, and every row is sent to a shell as written; point "
            "TMPDIR or ONEPIPELINE_NODE_SCRATCH_DIR at one"
        )
    try:
        scratch = Scratch(Path(tempfile.mkdtemp(prefix="allowlist-probe-", dir=base)))
    except OSError as error:
        fail(
            f"could not create a scratch directory under {base} ({error}); make it writable "
            "or point TMPDIR or ONEPIPELINE_NODE_SCRATCH_DIR at one that is, then retry"
        )
    # llmlint: ignore-block[changed_behavior_has_e2e] Reached only when the `sleep` found on
    # PATH just above then cannot be executed, a race no journey can stage without replacing
    # the host's coreutils; the absent `sleep` it would otherwise be is refused above.
    try:
        sleeper = subprocess.Popen(["sleep", "3600"])
    except OSError as error:
        shutil.rmtree(scratch.root, ignore_errors=True)
        fail(
            f"could not start `sleep`, the kill row's target ({error}); check that it runs, "
            "then retry"
        )
    # llmlint: ignore-end[changed_behavior_has_e2e]
    wrong = 0
    try:
        rows = prepare(scratch, sleeper.pid)
        if arguments.row:
            rows = [row for row in rows if row.invocation.id in set(arguments.row)]
        for tool in tools:
            held = [row for row in rows if tool in row.invocation.tools]

            def alive() -> bool:
                return sleeper.poll() is None

            if tool == "claude-code":
                claude = drive_claude(scratch, held, arguments.batch, alive)
                identity, stopped, stopped_as = claude.identity, claude.refused, Outcome.REFUSED
                vouched, requested = claude.vouched, claude.requested
            else:
                codex = drive_codex(scratch, held, alive)
                identity, stopped, stopped_as = codex.identity, codex.executed, Outcome.NOT_THROUGH
                vouched, requested = frozenset(), frozenset()
            ran = set(scratch.marks.read_text(encoding="utf-8").splitlines())
            outcome = {
                row.invocation.id: _let_through(
                    row, ran, stopped, alive, stopped_as, vouched, requested
                )
                for row in held
            }
            wrong += report(tool, identity, rows, outcome)
            _fresh(scratch, held)
            if sleeper.poll() is not None and tool != tools[-1]:
                fail(
                    "the kill row reached the probe's own sleep, so the next tool's kill row "
                    "would prove nothing; the row above says which tool let it through — take "
                    "`kill` off that tool's grants, then re-run, or run each tool alone with "
                    "`--tool` meanwhile"
                )
    except OSError as error:
        fail(
            f"the probe could not read or write {error.filename or 'its scratch tree'} "
            f"({error.strerror or error}); check that {base} is writable and has space and that "
            "each login it copies is readable, then retry"
        )
    finally:
        if sleeper.poll() is None:
            sleeper.send_signal(signal.SIGTERM)
        sleeper.wait()
        shutil.rmtree(scratch.root, ignore_errors=True)
    summarize(len(rows), wrong)
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
