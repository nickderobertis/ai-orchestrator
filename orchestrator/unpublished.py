"""`just unpublished`: the preserved-but-unpublished branches this host is holding onto.

`just recoverable` answers for one identity when it is run inside a registered
checkout, and from anywhere else for every identity — so a manager working inside a
checkout can miss preserved branches of other identities, each pinning a run root
whose worktree still carries its build output. This
view closes that gap: it reads what `onevcs` says is preserved, joins each row to the
session that preserved it, and states what each one costs in disk beside the `just`
command that lands it. It is a view over `onevcs` verbs and never a re-derivation from
`git` or a walk of `events.jsonl`; the one `git` it runs is a `rev-parse` used only as
the key of an acknowledgement, and it decides nothing about what is unpublished.

**This module is the one declaration of the contract a later node builds on**: the row
shape (:data:`ROW_FIELDS`), the counting rule (:func:`counted`), the exit-status
vocabulary (:data:`EXIT_STATUSES`), the acknowledgement's location and on-disk shape
(:func:`acknowledgement_file`, :data:`ACKNOWLEDGEMENT_VERSION`), the build-output
directory names (:data:`BUILD_OUTPUT_DIRECTORIES`) and the session label keys
(:data:`SESSION_LABELS`). `scripts/unpublished.sh --print-surface` renders the
vocabulary from here, and nothing else in the tree restates any of it.

**Targets.** `--host` answers for every registered identity from any working directory:
it asks `onevcs recoverable --repo <identity> --json` of each identity `onevcs repos`
lists, because the unscoped verb answers for one identity when run inside a registered
checkout. The **own-sessions** target — the default, `--own`, and a `--session` value
that is a manager session id rather than an `s-` token — is one filtered read,
`onevcs recoverable --json --label launcher=<manager session>`: the branches of every
session the engine opened for a run that manager session launched, found by the label
the engine stamps on the session and the adopted `onevcs` filters on. The manager
session is the `--session` value, else `ONEPIPELINE_LAUNCHER_SESSION` as
`scripts/launcher-session.sh` establishes it — the identity `onepipeline unwatched
--session` is keyed on. `--session <TOKEN>` (repeatable) is `onevcs recoverable --json
--session <TOKEN>…`, the branches those sessions hold or held. Both filtered reads run
from the filesystem root, outside every registered checkout, because the unscoped verb
run inside one answers for that checkout's identity alone. A session opened before the
labelling engine was adopted carries no labels, so the own target never reaches a
branch it preserved and nothing backfills them: `--host` does, and so does its
`--session <TOKEN>`.

**Rows.** Each is what `onevcs` states — identity, branch, base, provenance, its
`landed` object, the change URL, why it stopped, the session token it names (`null` for
a branch no session record names) — with `run`, `node` and `manager_session` read off
the session's labels under :data:`LABEL_RUN`, :data:`LABEL_NODE` and
:data:`LABEL_LAUNCHER`, `null` where a label is absent. A row `onevcs` reports a `held_by`
for is **in flight** — shown, marked, never counted — whatever its `holding` value, every
variant of that enum being a session that has not finished with the branch. Every
`landed.state` the listing carries counts — `no`, `unknown` (the *may have landed* rows)
and `in-part` — because each is a branch nothing here can show landed. The resume command is
rendered in its `just` form, the rewrite `scripts/recoverable.sh` also performs and
`tests/test_unpublished.py` holds the two copies together, because the raw `onevcs
publish-branch` line lands with an empty description. The disk reading is the run root
— the parent of the holder's worktree, clone and worktree together — in bytes, and
each build-output directory under its worktree called out on its own; the walk never
follows a symbolic link, and `--no-disk` skips it.

**The acknowledgement** records that the calling manager session has seen and
deliberately left a branch. It is keyed on that session and on the branch's tip, so a
branch that moves past the recorded tip counts again, and it is invisible to every
other session. Acknowledging is never landing: the row still shows, marked with its
reason, with `counted` false. A blank reason is refused — an acknowledgement carrying
only a branch is indistinguishable downstream from one nobody meant — and so is a
branch no row of the host-level reading names.

**The stop verdict.** `--stop-verdict` is this view answering one question for the
`Stop` hook, as the source `.claude/settings.json` declares to the engine's `onepipeline
stop-guard --source`: it reads that verb's neutral input — one object,
`{"session": <ID>, "continuation": <bool>}` — on standard input, and answers the own-sessions
target for that session, without the disk walk, as one neutral verdict on standard
output at exit `0`: `{"verdict":"block","reason":<the listing>}` where a row is counted,
`{"verdict":"none"}` where none is. It answers only this half, because the verb asks
`unwatched` itself. Input it cannot read or that names no session, and a listing it
cannot produce, are each a `block` whose reason says why, never `none`, since nothing
owed would not be known; the continuation is the verb's to judge, so it is read and
set aside.

Stdlib-only, and importable with `PYTHONPATH` at the checkout root and nothing else:
the `Stop` hook reaches this without `uv run`, because `uv` takes an exclusive lock on
the project environment and a hook reading this view runs at the end of every turn.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple, NewType, Protocol, TypedDict, cast, get_args

from orchestrator.root import REPO_ROOT

__all__ = [
    "ACKNOWLEDGEMENT_VERSION",
    "BUILD_OUTPUT_DIRECTORIES",
    "COUNTED",
    "COUNTED_LANDED_STATES",
    "EXIT_STATUSES",
    "IN_FLIGHT_HOLDINGS",
    "LABEL_LAUNCHER",
    "LABEL_NODE",
    "LABEL_RUN",
    "NOTHING_COUNTED",
    "RECIPES",
    "REFUSED",
    "ROW_FIELDS",
    "SESSION_LABELS",
    "UNANSWERED",
    "acknowledgement_file",
    "counted",
    "main",
    "print_surface",
    "resume_command",
    "stop_verdict",
]

#: Nothing is counted for the target: every preserved branch is in flight or
#: acknowledged at its current tip, or there is none.
NOTHING_COUNTED = 0
#: At least one row is counted — preserved, not in flight, not acknowledged at its
#: current tip. The one status a consumer acts on.
COUNTED = 7
#: The invocation was refused: the own-sessions target with no manager session to filter
#: on, an acknowledgement without a reason or of a branch no row names, a target this
#: cannot make sense of.
REFUSED = 2
#: The view could not answer — `onevcs` missing, refusing, or past its bound — and
#: standard error says why. Never `0` or `7`, because neither would be true.
UNANSWERED = 1


class Status(NamedTuple):
    """One exit status this view answers with: the number, its name, and what it says.

    A consumer branches on :attr:`code` and names it by :attr:`name`, so those two are
    what `scripts/unpublished.sh --print-surface` prints and what the drift test node
    `unfinished-guard` writes reads from there. :attr:`phrase` is the condition in
    words, for a reader of this declaration rather than for that machine-read line.
    """

    code: int
    name: str
    phrase: str


#: The exit-status vocabulary: one status per entry, each carrying the code a consumer
#: branches on, the name it is spelled by, and the condition in words, so that neither
#: has to be read off the code. `scripts/unpublished.sh --print-surface` prints the code
#: and the name as `status <code> <name>`, and the drift test the guard node writes reads
#: them from there.
EXIT_STATUSES: tuple[Status, ...] = (
    Status(NOTHING_COUNTED, "nothing-counted", "nothing is counted for the target"),
    Status(
        COUNTED,
        "counted",
        "at least one preserved branch is counted: not in flight, not acknowledged at its tip",
    ),
    Status(REFUSED, "refused", "the invocation was refused"),
    Status(UNANSWERED, "unanswered", "the view could not answer; standard error says why"),
)

# The session labels the engine stamps on every session a node opens. Their source is
# the engine's own declaration — `SESSION_RUN_LABEL`, `SESSION_NODE_LABEL` and
# `SESSION_LAUNCHER_LABEL` in `src/executor.rs` — and `tests/test_engine_contracts.py`
# reads those at `config/onepipeline.version`'s tag and holds these three to them.
#: The label carrying the run a session was opened for.
LABEL_RUN = "run"
#: The label carrying the node the session worked.
LABEL_NODE = "node"
#: The label carrying the manager session that launched the run.
LABEL_LAUNCHER = "launcher"
#: The three, in the order the row's `run`, `node` and `manager_session` fields follow.
SESSION_LABELS: tuple[str, str, str] = (LABEL_RUN, LABEL_NODE, LABEL_LAUNCHER)

#: The build-output directories called out under a run root's worktree, each with its
#: own bytes: the number a person acts on is the disk, and these are where it is.
BUILD_OUTPUT_DIRECTORIES: tuple[str, ...] = ("target", "node_modules", ".venv", ".nx", "dist")

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate,modern_domain_modeling] Its
# source is cited below — `Holding` in `crates/onevcs/src/session.rs` at the pinned
# release — and this is a declaration rather than a decision: nothing branches on which
# value it is, because `Row.in_flight` is "`onevcs` reported a `held_by` at all". So a
# variant onevcs adds is in flight without this tuple moving, and a gate reading onevcs's
# source would guard a list that decides nothing. What it is for is `--print-surface`,
# which states the vocabulary a consumer reads rather than restates it.
#: The whole of `onevcs`'s `held_by.holding` enum at the pinned release (0.30.1):
#: `Holding` in `crates/onevcs/src/session.rs` — `owner-running`, the process that opened
#: the session still running, and `run-root-occupied`, that opener stale while something
#: still holds the run root's lease. Either is a session that has not finished with its
#: branch.
IN_FLIGHT_HOLDINGS: tuple[str, ...] = ("owner-running", "run-root-occupied")
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate,modern_domain_modeling]

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate,modern_domain_modeling] Not
# a copy of onevcs's `landed.state` enum but this view's selection from it, and the
# selection is a manager's ruling rather than a fact about onevcs: *may have landed* and
# *in part* count. Deriving it from onevcs would defeat what it is for — a state onevcs
# adds must be ruled on, and until it is it reads as `unknown` — and a gate would hold a
# policy of this repository to another repository's vocabulary.
#: The `landed.state` values this view counts: `no` is unlanded, `unknown` is a *may have
#: landed* row whose record is gone, and `in-part` is a real landing with commits above
#: it, which is what publishing now would land.
COUNTED_LANDED_STATES: tuple[str, ...] = ("no", "unknown", "in-part")
#: The one state that does not count: the branch landed. Any state named neither here nor
#: above is one this view has no ruling on, and is read as `unknown` — which counts — and
#: said, because a state onevcs adds must never quietly take a preserved branch out of
#: the count.
LANDED_STATE = "yes"
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate,modern_domain_modeling]

#: The published landing verb `onevcs recoverable` renders, and the `just` recipe that
#: reaches it through the drafting this host adds. `scripts/recoverable.sh` holds the same
#: table for the human listing it rewrites; `tests/test_unpublished.py` reconciles the two.
RECIPES: dict[str, str] = {
    "publish-branch": "publish-branch",
    "recover": "repo-recover",
    "integrate": "integrate",
}

#: The row shape, in the order `--json` emits it. Exactly these fields, no more: a
#: consumer of the row reads them by name, and a field added here is a change to a
#: shared contract.
ROW_FIELDS: tuple[str, ...] = (
    "identity",
    "branch",
    "base",
    "provenance",
    "landed",
    "change_url",
    "stopped_because",
    "session",
    "run",
    "node",
    "manager_session",
    "resume_command",
    "in_flight",
    "counted",
    "acknowledgement",
    "disk",
)

#: The acknowledgement file's schema version, and the path under the XDG state home
#: it lives at: one file per manager session, named by the digest of the session id
#: rather than by the id, because the id is somebody else's string and joining one onto
#: a path is how a name becomes a directory traversal.
ACKNOWLEDGEMENT_VERSION = 1
ACKNOWLEDGEMENTS_UNDER: tuple[str, ...] = ("ai-orchestrator", "stop-unfinished", "acknowledged")

#: The environment name the manager session is read from — established by
#: `scripts/launcher-session.sh`, which `scripts/unpublished.sh` sources, and the same
#: name `onepipeline` attributes a launch to.
LAUNCHER_SESSION_ENV = "ONEPIPELINE_LAUNCHER_SESSION"

#: The shape a manager session id must have before anything is keyed on it: the shape
#: `scripts/launcher-session.sh`'s `launcher_session_is_usable` checks, which
#: `tests/test_unpublished.py` holds this to. The helper *keeps* an inherited value of
#: another shape — its run was launched under it — so this module checks it again,
#: and a module run directly rather than through the wrapper is checked the same way.
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

#: What `onevcs repos` answers when nothing is registered, and the one reading of an
#: identity-less listing this view accepts: every other one is format drift, which it
#: refuses rather than reports as nothing held.
NO_IDENTITIES = "no repositories registered"

#: The whole of `onevcs`'s session lifecycle, as `session holders --json` spells it:
#: `Lifecycle` in `crates/onevcs/src/session.rs`, which
#: `tests/e2e/unpublished_view/test_unpublished_onevcs_vocabulary.py` reads at
#: `config/onevcs.version`'s tag and
#: holds this to. A state outside it is said and read as none, which only decides which of
#: several records naming one branch a row is joined to.
HolderState = Literal["open", "closed"]

#: What an `onevcs` session token looks like, and nothing else: `s-` and the first twelve
#: lowercase hex characters of a SHA-256 — `session_token`, `short_digest` and `digest` in
#: `crates/onevcs/src/ids.rs`, which
#: `tests/e2e/unpublished_view/test_unpublished_onevcs_vocabulary.py` reads at
#: `config/onevcs.version`'s tag and holds this to in both directions. A `--session` value
#: of any other shape is read as a manager session id, which is the own-sessions target.
SESSION_TOKEN = re.compile(r"^s-[0-9a-f]{12}$")

#: What a recorded tip looks like: the object name `git rev-parse` answers with, which is
#: the only thing an acknowledgement is ever keyed on.
TIP = re.compile(r"^[0-9a-f]{40,64}$")

#: How long one `onevcs` read is given. `recoverable --json` from outside every
#: checkout takes about forty seconds for twenty rows on the host this was written for,
#: a cost of the installed release rather than of this view; the bound is there so a
#: wedged read answers :data:`UNANSWERED` rather than holding a turn open for ever.
ONEVCS_TIMEOUT_SECONDS = 900

#: Where the acknowledgement's `at` is stamped from, and how: ISO 8601, UTC, seconds.
TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


class Writer(Protocol):
    """Where this view writes: the stream a caller hands it, or the process's own.

    The seam exists so a test reads what an invocation answered without a subprocess,
    and `print(..., file=…)` is the whole of what is asked of it — so the structural
    `write` is the whole declaration, and every `io.TextIOBase` satisfies it already.
    """

    def write(self, text: str, /) -> object:
        """Take ``text``; what a stream answers with is its own business."""


class Reader(Protocol):
    """Where `--stop-verdict` reads the stop guard's input: a caller's stream, or stdin.

    The input is one JSON document read whole, so `read` is all that is asked of it.
    """

    def read(self) -> str:
        """Everything the stream holds."""


Identity = NewType("Identity", str)
Branch = NewType("Branch", str)
SessionToken = NewType("SessionToken", str)


class Refused(Exception):
    """An invocation this view will not perform, with the reason a person reads."""


class Unanswered(Exception):
    """A question this view could not answer, with the reason a person reads."""


class Holder(NamedTuple):
    """One session record `onevcs session holders` reports, as much of it as a row needs."""

    token: SessionToken
    identity: Identity
    branch: Branch
    worktree: Path
    #: ``None`` for a state outside :data:`HolderState`, which is read as not open.
    state: HolderState | None


class Acknowledgement(NamedTuple):
    """One recorded acknowledgement: a branch of an identity, left at a tip, for a reason."""

    branch: Branch
    identity: Identity
    tip: str
    reason: str
    at: str

    def entry(self) -> dict[str, str]:
        """The file's, and the row's, spelling of it."""
        return {
            "branch": self.branch,
            "identity": self.identity,
            "tip": self.tip,
            "reason": self.reason,
            "at": self.at,
        }


class Disk(NamedTuple):
    """Allocated file bytes pinned by a run root, and its build output by name.

    The run root is clone and worktree together. File counts use allocated blocks, so a
    sparse file does not claim its apparent length as disk cost. `run_root_bytes` is
    ``None`` where the reading could not be taken, which a reader must tell apart from
    a measured zero.
    """

    run_root: Path | None
    run_root_bytes: int | None
    build_output: dict[str, int]

    def entry(self) -> dict[str, Any]:
        """The row's spelling of it, which is what `--json` emits."""
        return {
            "run_root": None if self.run_root is None else str(self.run_root),
            "run_root_bytes": self.run_root_bytes,
            "build_output": self.build_output,
        }


def _is_run_root(directory: Path) -> bool:
    """Whether ``directory`` is `<…>/runs/<s-token>`, the one shape a run root has."""
    return directory.parent.name == "runs" and SESSION_TOKEN.fullmatch(directory.name) is not None


class Row(NamedTuple):
    """One preserved unpublished branch, before the disk reading and the decision."""

    identity: Identity
    branch: Branch
    base: str
    provenance: str
    # llmlint: ignore-block[modern_domain_modeling] `landed` and `held_by` are `onevcs`'s
    # own objects, and the row contract is that each reaches a consumer exactly as `onevcs`
    # stated it — `landed` "including `unlanded` where given". Modelling either here would
    # mint a second declaration of a schema this repository does not own and cannot gate,
    # and a field `onevcs` adds would be dropped by the model rather than passed through.
    # What this module *decides* from them is `in_flight`, `run_root` and `session`, and
    # those are typed properties below.
    landed: dict[str, Any]
    change_url: str | None
    stopped_because: str
    #: Where `onevcs` found the branch: the checkout the acknowledgement's tip is read in.
    checkout: Path
    resume: str | None
    held_by: dict[str, str] | None
    # llmlint: ignore-end[modern_domain_modeling]
    holder: Holder | None
    #: The session token `onevcs` names on the row, where it names one.
    token: SessionToken | None
    #: The session's labels, each a string `onevcs` stated; empty for an unlabelled one.
    labels: dict[str, str]

    @property
    def in_flight(self) -> bool:
        """Whether `onevcs` reports a session that has not finished with this branch.

        The presence of `held_by` is the whole of it, rather than which
        :data:`IN_FLIGHT_HOLDINGS` value it carries: every variant of that enum is a
        session still holding the branch, so reading the value would leave a variant
        onevcs adds counted — and a publication landing that branch this moment is
        exactly what a consumer of this view must not refuse a turn over.
        """
        return self.held_by is not None

    @property
    def worktree(self) -> Path | None:
        """The worktree `onevcs` names for this branch: its holder's, else its record's."""
        worktree = None if self.held_by is None else self.held_by.get("worktree")
        # Absolute, for the reason `_parse_holders` requires it of a session record: a
        # relative one would be read against whatever directory the view was run from.
        if isinstance(worktree, str) and os.path.isabs(worktree):
            return Path(worktree)
        return None if self.holder is None else self.holder.worktree

    @property
    def run_root(self) -> Path | None:
        """The run root holding this branch's work: clone and worktree together.

        Only a worktree at `<…>/runs/<s-token>/worktree` has one. That is the one layout a
        session's branch pins, and it is walked recursively, so anything else `onevcs`
        names — a pooled slot, which a closed session returns for reuse and whose disk is
        the next session's, or a path no run root owns — is not measured at all.
        """
        worktree = self.worktree
        if worktree is None or not _is_run_root(worktree.parent) or worktree.name != "worktree":
            return None
        return worktree.parent

    @property
    def session(self) -> SessionToken | None:
        if self.token is not None:
            return self.token
        if self.held_by is not None and isinstance(self.held_by.get("token"), str):
            return SessionToken(self.held_by["token"])
        return None if self.holder is None else self.holder.token


def counted(row: Row, acknowledgement: Acknowledgement | None) -> bool:
    """The counting rule: preserved, not in flight, and not acknowledged at its tip.

    ``acknowledgement`` is the one that stands for this row at its *current* tip, or
    ``None`` — a recorded acknowledgement the branch has moved past is ``None`` here,
    which is what makes the branch count again.
    """
    return (
        not row.in_flight
        and row.landed.get("state") in COUNTED_LANDED_STATES
        and acknowledgement is None
    )


def resume_command(recover_command: Sequence[str]) -> str | None:
    """`onevcs`'s published argv, rendered as the `just` recipe that drafts a body.

    Anchored on the verb, as `scripts/recoverable.sh` is: the argv is `["onevcs",
    <verb>, …]`, and a verb the table does not name — or an empty argv, which is what a
    landed row carries — renders nothing rather than something mangled.
    """
    if len(recover_command) < 2 or recover_command[0] != "onevcs":
        return None
    if not all(isinstance(word, str) for word in recover_command):
        # `shlex.join` raises on a non-string, which would end the whole listing rather
        # than this one row; a command this cannot render is no command.
        return None
    recipe = RECIPES.get(recover_command[1])
    if recipe is None:
        return None
    return shlex.join(["just", recipe, *recover_command[2:]])


def acknowledgement_file(session: str) -> Path:
    """Where ``session``'s acknowledgements live.

    `XDG_STATE_HOME` honoured only when absolute — the specification's own rule, and a
    boundary check: a relative one would put this record under whatever directory the
    caller ran in — else `~/.local/state`, exactly as the `Stop` hook keeps its memory.
    """
    state = os.environ.get("XDG_STATE_HOME", "")
    root = Path(state) if os.path.isabs(state) else Path.home() / ".local" / "state"
    named = hashlib.sha256(session.encode("utf-8")).hexdigest()
    return root.joinpath(*ACKNOWLEDGEMENTS_UNDER) / f"{named}.json"


def print_surface(out: Writer | None = None) -> None:
    """The vocabulary a consumer reads from `scripts/unpublished.sh --print-surface`."""
    out = sys.stdout if out is None else out
    for status in EXIT_STATUSES:
        print(f"status {status.code} {status.name}", file=out)
    for label in SESSION_LABELS:
        print(f"label {label}", file=out)
    for name in BUILD_OUTPUT_DIRECTORIES:
        print(f"build-output {name}", file=out)
    for holding in IN_FLIGHT_HOLDINGS:
        print(f"in-flight {holding}", file=out)
    for state in COUNTED_LANDED_STATES:
        print(f"counts {state}", file=out)
    print(f"acknowledgement-version {ACKNOWLEDGEMENT_VERSION}", file=out)
    print(f"acknowledgements-under {'/'.join(ACKNOWLEDGEMENTS_UNDER)}", file=out)
    for field in ROW_FIELDS:
        print(f"field {field}", file=out)


def _binary(name: str) -> str | None:
    """The installed ``name``: this checkout's own `.venv/bin` first, the search path after.

    Not `uv run`: `uv` takes an exclusive lock on the project environment, and a hook
    that reads this view runs at the end of every turn.
    """
    installed = REPO_ROOT / ".venv" / "bin" / name
    if installed.is_file() and os.access(installed, os.X_OK):
        return str(installed)
    return shutil.which(name)


def _onevcs(*arguments: str, cwd: Path | None = None) -> str:
    """Run one `onevcs` verb and answer its standard output, or raise :class:`Unanswered`."""
    binary = _binary("onevcs")
    if binary is None:
        raise Unanswered(
            f"found no `onevcs` to ask, at {REPO_ROOT / '.venv' / 'bin' / 'onevcs'} or on the "
            "search path; run `just bootstrap` from the checkout root"
        )
    command = [binary, *arguments]
    try:
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=ONEVCS_TIMEOUT_SECONDS,
            check=False,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as expired:
        raise Unanswered(
            f"`{shlex.join(command)}` ran past its {ONEVCS_TIMEOUT_SECONDS}s bound"
        ) from expired
    except OSError as failure:
        raise Unanswered(f"could not run `{shlex.join(command)}`: {failure}") from failure
    if done.returncode != 0:
        said = done.stderr.strip() or done.stdout.strip() or "nothing at all"
        raise Unanswered(f"`{shlex.join(command)}` exited {done.returncode} saying: {said}")
    return done.stdout


def _json_array(text: str, what: str) -> list[dict[str, Any]]:
    """The JSON array ``what`` answered, refused as unanswered when it is not one."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as malformed:
        raise Unanswered(f"{what} answered something that is not JSON: {malformed}") from malformed
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise Unanswered(f"{what} answered JSON that is not an array of objects")
    return parsed


def _identities() -> list[Identity]:
    """Every registered identity, off `onevcs repos`.

    The listing is a human table with no `--json`: an identity line is unindented and
    tab-separated, `<key>\\t<gate>`, and each of its checkouts follows indented. The one
    unindented line that carries no tab is :data:`NO_IDENTITIES`, the empty answer.

    A line of any other shape is format drift, and dropping it is what this
    must never do: the identities are what every `--session` token is looked for in, so
    a listing read short answers *no registered identity records that token* — a false
    answer wearing :data:`NOTHING_COUNTED`, over branches that are really held. So a
    line this cannot read makes the whole listing :data:`UNANSWERED`, which says the
    view could not answer rather than that there was nothing to find.
    """
    listing = _onevcs("repos").splitlines()
    if not any(line.strip() for line in listing):
        raise Unanswered("`onevcs repos` answered no rows and no declared empty response")
    identities: list[Identity] = []
    for line in listing:
        if not line.strip():
            continue
        if line[0].isspace():
            # A checkout row has two tab-separated fields after its indentation.
            # Anything else may be an identity in a format this reader does not know.
            if not re.fullmatch(r"  [^\t\s][^\t]*\t\S[^\t]*", line):
                raise Unanswered(
                    f"`onevcs repos` answered a checkout line this view cannot read: {line!r}"
                )
            continue
        if "\t" not in line:
            if line == NO_IDENTITIES:
                continue
            raise Unanswered(
                f"`onevcs repos` answered a line this view cannot read as an identity, "
                f"as one of their checkouts, or as {NO_IDENTITIES!r}: {line!r}"
            )
        identity_row = re.fullmatch(r"([^\s\t]+)\t(\S[^\t]*)", line)
        if identity_row is None:
            raise Unanswered(
                f"`onevcs repos` answered an identity line this view cannot read: {line!r}"
            )
        identities.append(Identity(identity_row.group(1)))
    return identities


def _read_holders(identity: Identity, warnings: list[str]) -> list[Holder]:
    """Every session record of ``identity``, or :class:`Unanswered` when none can be read."""
    reported = _json_array(
        _onevcs("session", "holders", identity, "--json"),
        f"`onevcs session holders {identity} --json`",
    )
    return _parse_holders(reported, identity, warnings)


def _holders(identity: Identity, warnings: list[str]) -> list[Holder]:
    """Every session record of ``identity``, or none — with why on ``warnings``.

    For the host target, where a record only names a row's session: the row stands
    without it. The session target cannot say that, and reads :func:`_read_holders`.
    """
    try:
        return _read_holders(identity, warnings)
    except Unanswered as failure:
        warnings.append(f"identity {identity}: its sessions could not be read: {failure}")
        return []


def _text(record: dict[str, Any], key: str, default: str) -> str:
    """``key``'s value when `onevcs` gave a string there, else ``default``.

    Every field this view only ever *renders* is taken at this boundary rather than
    coerced: `str()` on a nested object writes that object's Python repr into a row a
    person reads and into the JSON another consumer parses, which is worse than the
    default, because it looks like a value `onevcs` stated.
    """
    value = record.get(key)
    return value if isinstance(value, str) else default


def _parse_holders(
    reported: Iterable[dict[str, Any]], identity: Identity, warnings: list[str]
) -> list[Holder]:
    """The holder records as this view reads them: a record naming no `s-` token, no
    branch or no absolute worktree is left out and said, because each of the three is a
    field a row joins on."""
    holders: list[Holder] = []
    for record in reported:
        token, branch, worktree = record.get("token"), record.get("branch"), record.get("worktree")
        if not (
            isinstance(token, str)
            and SESSION_TOKEN.fullmatch(token)
            and isinstance(branch, str)
            and branch
            and isinstance(worktree, str)
            and os.path.isabs(worktree)
        ):
            warnings.append(
                f"identity {identity}: a session record names no session token, branch or "
                f"absolute worktree and was left out: {json.dumps(record)}"
            )
            continue
        stated_identity = record.get("identity")
        if isinstance(stated_identity, str) and stated_identity and stated_identity != identity:
            raise Unanswered(
                f"identity {identity}: a session record names different identity "
                f"{stated_identity!r} for token {token}"
            )
        holders.append(
            Holder(
                token=SessionToken(token),
                # An empty identity joins nothing, so it is the one that was asked for.
                identity=Identity(_text(record, "identity", "") or identity),
                branch=Branch(branch),
                worktree=Path(worktree),
                state=_holder_state(record, identity, warnings),
            )
        )
    return holders


def _holder_state(
    record: dict[str, Any], identity: Identity, warnings: list[str]
) -> HolderState | None:
    """A holder record's lifecycle state, held to :data:`HolderState`.

    Read only by `_holder_for`, which prefers an `open` record, so a state outside the
    vocabulary is kept as ``None`` — not open, rather than anything it is not — and said.
    """
    state = record.get("state")
    for known in get_args(HolderState):
        if state == known:
            # `get_args` answers the Literal's members untyped; each is one by construction.
            return cast(HolderState, known)
    warnings.append(
        f"identity {identity}: session {record.get('token')} is in the state "
        f"{json.dumps(state)}, which is none of {', '.join(get_args(HolderState))}, so it "
        "is read as not open"
    )
    return None


def _recoverable(identity: Identity) -> list[dict[str, Any]]:
    """`onevcs recoverable --repo <identity> --json`: that identity's, wherever this runs.

    Always scoped by `--repo`, because the unscoped verb answers for the identity of the
    registered checkout it is run inside and for every identity only from outside all of
    them — so an unscoped read would make the answer depend on the working directory.
    """
    arguments = ["recoverable", "--repo", identity, "--json"]
    records = _json_array(_onevcs(*arguments), f"`onevcs {shlex.join(arguments)}`")
    for record in records:
        if record.get("identity") != identity:
            raise Unanswered(
                f"`onevcs {shlex.join(arguments)}` answered a row for identity "
                f"{record.get('identity')!r}, not {identity!r}"
            )
    return records


def _holder_for(
    holders: Iterable[Holder],
    identity: Identity,
    branch: Branch,
    token: SessionToken | None = None,
) -> Holder | None:
    """The session record behind a row: the one `onevcs` names by token, where it names one.

    That record must hold the row's own branch, or it is another branch's worktree and
    is not used. Otherwise the record holding ``branch``, an open one first, else the
    first named.
    Several records can name one branch — a retried session holds a branch named for an
    earlier token — which is why the branch is read off the record and never derived
    from the token.
    """
    holders = list(holders)
    if token is not None:
        named = [
            h for h in holders if h.identity == identity and h.token == token and h.branch == branch
        ]
        if named:
            return named[0]
    matching = [h for h in holders if h.identity == identity and h.branch == branch]
    for holder in matching:
        if holder.state == "open":
            return holder
    return matching[0] if matching else None


def _url(branch_object: dict[str, Any]) -> str | None:
    """The change request's URL where `onevcs` gave one, and ``None`` otherwise.

    `null` and *absent* are one answer to a consumer of the row — there is no change
    request — and anything that is not a string is the same answer, rather than a value
    passed into the row unread.
    """
    url = branch_object.get("change_url")
    return url if isinstance(url, str) else None


def _landed(value: object, identity: str, branch: str, warnings: list[str]) -> dict[str, Any]:
    """`onevcs`'s `landed` object, carried whole once it names a string `state`.

    One that does not, or names a state this view has no ruling on, is read as the *may have
    landed* row — `{"state": "unknown"}`, which counts — and said, because a landing nothing
    can read is a branch nothing here can show landed, and leaving the row out would hide
    exactly that branch.
    """
    if isinstance(value, dict) and isinstance(value.get("state"), str):
        if value["state"] in (*COUNTED_LANDED_STATES, LANDED_STATE):
            return value
        warnings.append(
            f"{identity} {branch}: its landing state {value['state']!r} is one this view "
            f"has no ruling on, so it is shown as unknown and counted: {json.dumps(value)}"
        )
        return {"state": "unknown"}
    warnings.append(
        f"{identity} {branch}: its landing could not be read and is shown as unknown: "
        f"{json.dumps(value)}"
    )
    return {"state": "unknown"}


def _held_by(
    value: object, identity: str, branch: str, warnings: list[str]
) -> dict[str, str] | None:
    """`onevcs`'s `held_by`, keeping each field this view reads only in the shape it reads.

    Presence is the whole of the in-flight rule, so a `held_by` whose fields cannot be read
    still marks the row in flight; what is dropped is a field this view would otherwise
    act on — a `holding` that is not a string, a `token` that is not an `s-` token, a
    `worktree` that is not absolute and would be walked against the working directory —
    and each drop is said.
    """
    if value is None:
        return None
    fields = value if isinstance(value, dict) else {}
    kept: dict[str, str] = {}
    dropped = not isinstance(value, dict)
    for key, valid in (
        ("holding", lambda v: True),
        ("token", lambda v: SESSION_TOKEN.fullmatch(v) is not None),
        ("worktree", os.path.isabs),
    ):
        field = fields.get(key)
        if isinstance(field, str) and valid(field):
            kept[key] = field
        elif field is not None:
            dropped = True
    if dropped:
        warnings.append(
            f"{identity} {branch}: its holder could not be read whole and is shown in flight "
            f"on what could: {json.dumps(value)}"
        )
    return kept


#: How a `recoverable` row this view cannot read is said. The stop verdict reads it back,
#: because a row left out may be a branch the session owes and nothing can tell whose.
ROW_LEFT_OUT = "a row names no identity, branch or absolute checkout and was left out"


def _row(record: dict[str, Any], holders: Iterable[Holder], warnings: list[str]) -> Row | None:
    """One `recoverable` row as this view reads it, or ``None`` with why on ``warnings``."""
    branch_object = record.get("branch")
    identity = record.get("identity")
    checkout = record.get("checkout")
    if not (
        isinstance(branch_object, dict)
        and isinstance(branch_object.get("branch"), str)
        and branch_object["branch"]
        and isinstance(identity, str)
        and identity
        and isinstance(checkout, str)
        and os.path.isabs(checkout)
    ):
        warnings.append(f"{ROW_LEFT_OUT}: {json.dumps(record)}")
        return None
    recover = record.get("recover_command")
    branch = Branch(branch_object["branch"])
    landed = _landed(record.get("landed"), identity, branch, warnings)
    held_by = _held_by(record.get("held_by"), identity, branch, warnings)
    token = _token(record.get("session"), identity, branch, warnings)
    return Row(
        identity=Identity(identity),
        branch=branch,
        base=_text(branch_object, "base", ""),
        provenance=_text(branch_object, "provenance", ""),
        landed=landed,
        change_url=_url(branch_object),
        stopped_because=_text(record, "stopped_because", ""),
        checkout=Path(checkout),
        resume=resume_command(recover) if isinstance(recover, list) else None,
        held_by=held_by,
        holder=_holder_for(holders, Identity(identity), branch, token),
        token=token,
        labels=_labels(record.get("labels"), identity, branch, warnings),
    )


def _token(value: object, identity: str, branch: str, warnings: list[str]) -> SessionToken | None:
    """The session token `onevcs` names on a row, held to :data:`SESSION_TOKEN`.

    `null` is a branch no session record names; anything else that is not a token is
    said and read as none, so the row falls back to its holder rather than naming a
    session nothing can look up.
    """
    if value is None:
        return None
    if isinstance(value, str) and SESSION_TOKEN.fullmatch(value):
        return SessionToken(value)
    warnings.append(f"{identity} {branch}: its session {json.dumps(value)} is not a session token")
    return None


def _labels(value: object, identity: str, branch: str, warnings: list[str]) -> dict[str, str]:
    """The session's labels, keeping each key and value that is a string, and saying a drop.

    Absent or `{}` is a session opened before the labelling engine was adopted, which
    carries none, and is not a fault.
    """
    if value is None:
        return {}
    fields = value if isinstance(value, dict) else {}
    kept = {k: v for k, v in fields.items() if isinstance(k, str) and isinstance(v, str)}
    if not isinstance(value, dict) or len(kept) != len(fields):
        warnings.append(
            f"{identity} {branch}: its session labels could not be read whole and only the "
            f"string ones are used: {json.dumps(value)}"
        )
    return kept


def _host_rows(warnings: list[str]) -> list[Row]:
    """Every preserved branch on every registered identity, joined to its session record.

    Each identity `onevcs repos` lists is asked by name, so the answer is the host's from
    any working directory — inside a registered checkout included, where the unscoped
    `recoverable` would answer for that checkout's identity alone.
    """
    records: list[dict[str, Any]] = []
    holders: list[Holder] = []
    for identity in _identities():
        records.extend(_recoverable(identity))
        holders.extend(_holders(identity, warnings))
    return [row for r in records if (row := _row(r, holders, warnings)) is not None]


def _filtered(arguments: Sequence[str]) -> list[dict[str, Any]]:
    """One filtered `onevcs recoverable --json` read, from outside every registered checkout.

    Unscoped by `--repo` so it answers for every identity at once, which it does only
    from a directory no registered checkout contains — the filesystem root — because run
    inside one it answers for that checkout's identity alone.
    """
    command = ["recoverable", "--json", *arguments]
    return _json_array(_onevcs(*command, cwd=Path("/")), f"`onevcs {shlex.join(command)}`")


def _joined(
    records: Sequence[dict[str, Any]], with_holders: bool, warnings: list[str]
) -> list[Row]:
    """A filtered read's rows, each joined to its session record only when ``with_holders``.

    The record is what names a closed session's worktree, which only the disk reading
    needs, so a `--no-disk` read asks `onevcs` nothing more than the filtered listing.
    """
    rows = [row for r in records if (row := _row(r, [], warnings)) is not None]
    if not with_holders:
        return rows
    holders: list[Holder] = []
    for identity in sorted({row.identity for row in rows}):
        holders.extend(_holders(identity, warnings))
    return [
        row._replace(holder=_holder_for(holders, row.identity, row.branch, row.token))
        for row in rows
    ]


def _session_rows(
    tokens: Sequence[SessionToken], with_holders: bool, warnings: list[str]
) -> list[Row]:
    """The preserved branches the named sessions hold or held: `recoverable --session`.

    Taken as `onevcs` filtered it: a row names the session holding its branch *now*, which
    for a retried session is a later token than the one asked about, so nothing on the row
    states which asked token selected it, and a check against the tokens would drop
    exactly the retried branches.
    """
    arguments = [word for token in tokens for word in ("--session", token)]
    return _joined(_filtered(arguments), with_holders, warnings)


def _own_rows(manager: str, with_holders: bool, warnings: list[str]) -> list[Row]:
    """The preserved branches of every session a run ``manager`` launched opened.

    The one filtered read `recoverable --label launcher=<manager>`: the engine stamps the
    launching session on every session a node opens, and `onevcs` filters on it. Every row
    is held to carrying that label before it is counted for ``manager``: one that does not
    is a filter that did not filter, and counting it would owe this manager another's
    branch, so the whole read is :class:`Unanswered`.
    """
    arguments = ["--label", f"{LABEL_LAUNCHER}={manager}"]
    rows = _joined(_filtered(arguments), with_holders, warnings)
    for row in rows:
        if row.labels.get(LABEL_LAUNCHER) != manager:
            raise Unanswered(
                f"`onevcs recoverable {shlex.join(arguments)}` answered {row.branch} "
                f"[{row.identity}], whose session is labelled "
                f"{json.dumps(row.labels.get(LABEL_LAUNCHER))} rather than {manager!r}"
            )
    return rows


def _readable_bytes_under(root: Path, warnings: list[str]) -> int:
    """Sum allocated bytes of regular files; report unreadable parts and skip symlinks."""
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_blocks * 512
        except OSError as failure:
            warnings.append(f"{directory}: could not be read while measuring disk: {failure}")
    return total


def _disk(row: Row, warnings: list[str]) -> Disk:
    """The run root's bytes, and each build-output directory under its worktree."""
    run_root = row.run_root
    if run_root is None:
        if row.worktree is not None:
            warnings.append(
                f"{row.branch}: its worktree {row.worktree} is not under a run root "
                "(`<…>/runs/<s-token>/worktree`), so its disk was not measured"
            )
        return Disk(run_root=None, run_root_bytes=None, build_output={})
    if any(component.is_symlink() for component in (run_root, *run_root.parents)):
        warnings.append(
            f"{row.branch}: its run root {run_root} traverses a symbolic link, "
            "so its disk was not measured"
        )
        return Disk(run_root=run_root, run_root_bytes=None, build_output={})
    if not run_root.is_dir():
        warnings.append(
            f"{row.branch}: its run root {run_root} is not a directory, so its disk was not "
            "measured"
        )
        return Disk(run_root=run_root, run_root_bytes=None, build_output={})
    worktree = run_root / "worktree"
    if worktree.is_symlink():
        warnings.append(f"{row.branch}: its worktree {worktree} is a symbolic link")
        build_output: dict[str, int] = {}
    else:
        build_output = {
            name: _readable_bytes_under(candidate, warnings)
            for name in BUILD_OUTPUT_DIRECTORIES
            if (candidate := worktree / name).is_dir() and not candidate.is_symlink()
        }
    return Disk(
        run_root=run_root,
        run_root_bytes=_readable_bytes_under(run_root, warnings),
        build_output=build_output,
    )


def _read_acknowledgements(session: str | None, warnings: list[str]) -> list[Acknowledgement]:
    """Every acknowledgement ``session`` recorded; none for a session nothing identifies."""
    if session is None:
        return []
    path = acknowledgement_file(session)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as failure:
        warnings.append(f"{path}: the acknowledgement file could not be read: {failure}")
        return []
    if not isinstance(document, dict) or document.get("version") != ACKNOWLEDGEMENT_VERSION:
        warnings.append(f"{path}: not a version {ACKNOWLEDGEMENT_VERSION} acknowledgement file")
        return []
    entries = document.get("acknowledged")
    if not isinstance(entries, list):
        warnings.append(f"{path}: acknowledged is not an array")
        return []
    acknowledged: list[Acknowledgement] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if _is_acknowledgement(entry):
            key = (entry["identity"], entry["branch"])
            if key in seen:
                warnings.append(f"{path}: duplicate acknowledgement for {key}; none was applied")
                return []
            seen.add(key)
            acknowledged.append(Acknowledgement(**{k: entry[k] for k in Acknowledgement._fields}))
        else:
            warnings.append(f"{path}: an entry is not an acknowledgement and was left out")
    return acknowledged


def _is_acknowledgement(entry: object) -> bool:
    """Whether a recorded entry is one this view will let suppress a count.

    Read-side and write-side hold the same two rules, because what suppresses a count is
    what is on disk rather than what this process wrote: a reason :func:`_reason_fault`
    finds a fault in — a blank one among them — is refused at
    `--acknowledge` because an acknowledgement carrying only a branch is indistinguishable
    from one nobody meant, and an entry hand-edited to carry one would suppress a count
    that same way. The tip must look like the object name it is keyed on, since an entry
    naming anything else can never match a tip and only hides why the branch still counts.
    """
    if not isinstance(entry, dict):
        return False
    if not all(isinstance(entry.get(key), str) for key in Acknowledgement._fields):
        return False
    try:
        recorded_at = datetime.strptime(entry["at"], TIMESTAMP)
    except ValueError:
        return False
    return (
        recorded_at.strftime(TIMESTAMP) == entry["at"]
        and _reason_fault(entry["reason"]) is None
        and TIP.fullmatch(entry["tip"]) is not None
    )


def _write_acknowledgements(session: str, acknowledged: Sequence[Acknowledgement]) -> Path:
    """Write ``session``'s acknowledgements whole, or raise :class:`Unanswered` saying why.

    Written beside the file and moved over it, so a write that fails part-way leaves the
    earlier record standing rather than a truncated one every later read refuses.
    """
    path = acknowledgement_file(session)
    document = {
        "version": ACKNOWLEDGEMENT_VERSION,
        "acknowledged": [entry.entry() for entry in acknowledged],
    }
    pending = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        os.replace(pending, path)
    except OSError as failure:
        # The partial file, where one was made at all: a parent that is not a directory
        # fails the removal too, and the write's failure is the one to report.
        with contextlib.suppress(OSError):
            pending.unlink()
        raise Unanswered(
            f"the acknowledgement could not be written to {path}: {failure}; make its "
            "directory writable, or point XDG_STATE_HOME at an absolute directory that is, "
            "then acknowledge again"
        ) from failure
    return path


def _tip(row: Row) -> str:
    """What ``row``'s branch stands at, read in the checkout `onevcs` found it in.

    The one `git` this module runs, and it is used only as the acknowledgement's key: it
    decides nothing about what is unpublished. A tip that cannot be read is
    :class:`Unanswered`, and each caller says what that means for it.
    """
    command = [
        "git",
        "-C",
        str(row.checkout),
        "rev-parse",
        "--verify",
        "--quiet",
        f"{row.branch}^{{commit}}",
    ]
    try:
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as failure:
        raise Unanswered(f"could not run `{shlex.join(command)}`: {failure}") from failure
    tip = done.stdout.strip()
    # Held to the object-name shape before it is compared or written as a key: anything
    # else is not a tip, whatever the exit status said.
    if done.returncode != 0 or TIP.fullmatch(tip) is None:
        said = done.stderr.strip() or f"{tip!r} on standard output"
        raise Unanswered(
            f"{row.branch}: its tip could not be read in {row.checkout}; git said {said}"
        )
    return tip


def _standing(
    row: Row, acknowledged: Sequence[Acknowledgement], warnings: list[str]
) -> Acknowledgement | None:
    """The acknowledgement standing for ``row`` at its current tip, if one does.

    A tip that cannot be read leaves no acknowledgement standing — the branch counts,
    which is the conservative reading — and says so on standard error.
    """
    for entry in acknowledged:
        if entry.branch == row.branch and entry.identity == row.identity:
            try:
                tip = _tip(row)
            except Unanswered as failure:
                warnings.append(f"{failure}, so its acknowledgement does not stand")
                return None
            return entry if entry.tip == tip else None
    return None


def _reason_fault(reason: str) -> str | None:
    """What is wrong with ``reason`` as a recorded reason, or ``None`` when nothing is.

    A reason is one line of prose a person reads in the listing: one carrying no visible
    character is indistinguishable from none, and a control character — a newline among
    them — would split the row it is rendered into. The one rule both the write and the
    read of an acknowledgement hold a reason to.
    """
    stripped = reason.strip()
    if not any(ch.isprintable() and not ch.isspace() for ch in stripped):
        return f"the reason {reason!r} carries no visible character, so it says nothing"
    unprintable = sorted({ch for ch in stripped if not ch.isprintable()})
    if unprintable:
        return (
            f"the reason {reason!r} carries the unprintable character(s) "
            f"{', '.join(repr(ch) for ch in unprintable)}, and a reason is one line of text"
        )
    return None


def _reason(reason: str | None) -> str:
    """``reason`` as an acknowledgement records it, or :class:`Refused` naming its fault."""
    wanted = '`--reason "<why this branch is deliberately left>"`'
    if reason is None:
        raise Refused(
            f"an acknowledgement needs a reason: {wanted}; one carrying only a branch is "
            "indistinguishable from one nobody meant"
        )
    fault = _reason_fault(reason)
    if fault is not None:
        raise Refused(f"{fault}; an acknowledgement needs a reason: {wanted}")
    return reason.strip()


def _session(value: str | None) -> str | None:
    """The manager session named in the environment, or ``None`` when none is.

    :class:`Refused` for a value of another shape than :data:`SESSION_ID`: it is what an
    acknowledgement file is keyed on, and a malformed one would be a record no later read
    under a well-formed id can ever find.
    """
    if not value:
        return None
    if SESSION_ID.fullmatch(value) is None:
        raise Refused(
            f"{LAUNCHER_SESSION_ENV} is {value!r}, which is not the shape a session id has "
            "(1-200 characters of letters, digits, dot, underscore or hyphen, starting with a "
            "letter or digit); whatever exported it is what needs repairing"
        )
    return value


def _acknowledge(
    branch: Branch, reason: str | None, session: str | None, warnings: list[str]
) -> tuple[str, list[Row]]:
    """Record that ``session`` has seen and left ``branch``.

    Answers the receipt line and the host-level rows the branch was looked up in, so the
    caller's status is the whole target's rather than this one branch's.
    """
    recorded = _reason(reason)
    if session is None:
        raise Refused(
            "no manager session identifies this shell, so there is nothing to key the "
            f"acknowledgement on; {LAUNCHER_SESSION_ENV} is set by the harness this is run under"
        )
    rows = _host_rows(warnings)
    named = [row for row in rows if row.branch == branch]
    if not named:
        raise Refused(
            f"branch {branch} is on no row of the host-level reading, so there is nothing to "
            "acknowledge; `just unpublished --host` lists what is"
        )
    if len({row.identity for row in named}) > 1:
        raise Refused(
            f"branch {branch} is preserved on several identities "
            f"({', '.join(sorted({row.identity for row in named}))}); nothing here "
            "disambiguates one"
        )
    row = named[0]
    tip = _tip(row)
    entry = Acknowledgement(
        branch=row.branch,
        identity=row.identity,
        tip=tip,
        reason=recorded,
        at=datetime.now(UTC).strftime(TIMESTAMP),
    )
    kept: list[Acknowledgement] = []
    existing = acknowledgement_file(session)
    if existing.exists() or existing.is_symlink():
        warnings_before_read = len(warnings)
        kept = [
            a
            for a in _read_acknowledgements(session, warnings)
            if not (a.branch == entry.branch and a.identity == entry.identity)
        ]
        if len(warnings) != warnings_before_read:
            raise Unanswered(
                "the existing acknowledgement file could not be read completely; "
                "repair it before recording another acknowledgement"
            )
    path = _write_acknowledgements(session, [*kept, entry])
    receipt = f"acknowledged {row.branch} [{row.identity}] at {tip[:12]}: {entry.reason} ({path})"
    return receipt, rows


# llmlint: ignore[modern_domain_modeling] This is the serialized form itself, not a model
# of one: it is `json.dumps`'d as it stands, its schema is :data:`ROW_FIELDS` — one
# declaration, which the return below is built from and `tests/test_unpublished.py` holds
# the emitted row to — and two of its values are `onevcs`'s own objects carried whole. A
# typed model here would be a second statement of that schema with a serializer between
# them, and the dict would still be what a consumer parses.
def _rendered(
    row: Row, acknowledgement: Acknowledgement | None, disk: Disk | None
) -> dict[str, Any]:
    """The row as `--json` emits it: exactly :data:`ROW_FIELDS`, in that order."""
    rendered = {
        "identity": row.identity,
        "branch": row.branch,
        "base": row.base,
        "provenance": row.provenance,
        "landed": row.landed,
        "change_url": row.change_url,
        "stopped_because": row.stopped_because,
        "session": row.session,
        "run": row.labels.get(LABEL_RUN),
        "node": row.labels.get(LABEL_NODE),
        "manager_session": row.labels.get(LABEL_LAUNCHER),
        "resume_command": row.resume,
        "in_flight": row.in_flight,
        "counted": counted(row, acknowledgement),
        "acknowledgement": None if acknowledgement is None else acknowledgement.entry(),
        "disk": None if disk is None else disk.entry(),
    }
    return {field: rendered[field] for field in ROW_FIELDS}


def _human_bytes(count: int | None) -> str:
    """A byte count as a person reads one; `-` for a reading that was not taken."""
    if count is None:
        return "-"
    size = float(count)
    unit = "B"
    for larger in ("K", "M", "G", "T"):
        if size < 1024:
            break
        size /= 1024
        unit = larger
    return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"


def _table(rows: Sequence[dict[str, Any]], out: Writer) -> None:
    """The human rendering: a table, the ways out beside each counted row, a trailer."""
    if not rows:
        print("no preserved unpublished branch for this target", file=out)
        return
    headers = (
        "IDENTITY",
        "BRANCH",
        "BASE",
        "LANDED",
        "SESSION",
        "RUN ROOT",
        "BUILD OUTPUT",
        "STANDING",
    )
    lines: list[tuple[str, ...]] = []
    for row in rows:
        disk = row["disk"]
        standing = "counted"
        if row["in_flight"]:
            standing = "in flight"
        elif row["acknowledgement"] is not None:
            standing = f"acknowledged: {row['acknowledgement']['reason']}"
        elif not row["counted"]:
            standing = f"landed: {row['landed'].get('state')}"
        build = "-"
        run_root_bytes = "-"
        if disk is not None:
            run_root_bytes = _human_bytes(disk["run_root_bytes"])
            build = (
                " ".join(
                    f"{name}={_human_bytes(size)}" for name, size in disk["build_output"].items()
                )
                or "-"
            )
        lines.append(
            (
                row["identity"],
                row["branch"],
                row["base"],
                str(row["landed"].get("state")),
                row["session"] or "-",
                run_root_bytes,
                build,
                standing,
            )
        )
    widths = [max(len(cell) for cell in column) for column in zip(headers, *lines, strict=True)]
    for line in (headers, *lines):
        print(
            "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip(),
            file=out,
        )
    print(file=out)
    for row in rows:
        if not row["counted"]:
            continue
        print(f"{row['branch']} [{row['identity']}] — {row['stopped_because']}", file=out)
        if row["resume_command"]:
            print(f"    land it:         {row['resume_command']}", file=out)
        print(
            f"    or acknowledge:  just unpublished --acknowledge {shlex.quote(row['branch'])} "
            '--reason "<why it is deliberately left>"',
            file=out,
        )
    total = sum(1 for row in rows if row["counted"])
    in_flight = sum(1 for row in rows if row["in_flight"])
    acknowledged = sum(1 for row in rows if row["acknowledgement"] is not None)
    measured = [row["disk"] for row in rows if row["disk"] is not None]
    build_bytes = sum(size for disk in measured for size in disk["build_output"].values())
    run_root_bytes = sum(
        disk["run_root_bytes"] for disk in measured if disk["run_root_bytes"] is not None
    )
    held = (
        f"run roots hold {_human_bytes(run_root_bytes)}, of which build output is "
        f"{_human_bytes(build_bytes)}"
        if measured
        else "disk not measured (--no-disk)"
    )
    print(
        f"{total} counted of {len(rows)} preserved unpublished branch(es); {in_flight} in flight, "
        f"{acknowledged} acknowledged; {held}",
        file=out,
    )
    print(
        "Acknowledging is never landing: the row stays until its branch lands or is removed.",
        file=out,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="just unpublished",
        description="The preserved-but-unpublished branches this host is holding onto.",
        add_help=True,
    )
    target = parser.add_argument_group("target")
    target.add_argument("--host", action="store_true", help="every registered identity")
    target.add_argument(
        "--own",
        action="store_true",
        help="the sessions this manager session's runs opened, by their launcher label (the "
        "default)",
    )
    target.add_argument(
        "--session",
        action="append",
        default=[],
        metavar="TOKEN|ID",
        help="an onevcs session token (s-…), repeatable; or one manager session id, whose own "
        "sessions are the target",
    )
    parser.add_argument("--json", action="store_true", help="emit the rows as a JSON array")
    parser.add_argument("--no-disk", action="store_true", help="skip the disk walk")
    parser.add_argument(
        "--acknowledge",
        metavar="BRANCH",
        help="record that this session has seen and deliberately left BRANCH; needs --reason",
    )
    parser.add_argument("--reason", help="why BRANCH is deliberately left")
    parser.add_argument(
        "--print-surface", action="store_true", help="print the exit-status vocabulary and exit"
    )
    parser.add_argument(
        "--stop-verdict",
        action="store_true",
        help="read a stop guard's neutral input on standard input and answer the named "
        "session's own target as one neutral verdict",
    )
    return parser


class Own(NamedTuple):
    """The own-sessions target: every session a run this manager session launched opened."""

    manager: str


#: What one invocation asks about: every identity (``None``), the named `onevcs`
#: sessions, or a manager session's own.
Target = list[SessionToken] | Own | None


def _target(arguments: argparse.Namespace, named: str | None) -> Target:
    """The target an invocation names, ``named`` being the environment's manager session.

    `--session` values are either all `s-` tokens or one manager session id: a mixture, or
    two ids, would be two targets answered as one.
    """
    if arguments.host and (arguments.own or arguments.session):
        raise Refused("`--host` and `--session`/`--own` are two targets; name one")
    if arguments.host:
        return None
    tokens = [SessionToken(v) for v in arguments.session if SESSION_TOKEN.fullmatch(v)]
    managers = [v for v in arguments.session if not SESSION_TOKEN.fullmatch(v)]
    if tokens and (managers or arguments.own):
        raise Refused(
            "`--session <s-token>` names onevcs sessions and `--own`/`--session <manager "
            "session id>` a manager's own; they are two targets, name one"
        )
    if tokens:
        return tokens
    if len(managers) > 1:
        raise Refused(
            f"`--session` names {len(managers)} manager sessions ({', '.join(managers)}); "
            "the own-sessions target is one manager's, so name one"
        )
    manager = managers[0] if managers else named
    if not manager:
        raise Refused(
            "the own-sessions target — the default, and `--own` — is the sessions a manager "
            "session's runs opened, and no manager session identifies this shell: name one "
            f"with `--session <manager session id>`, or ask `--host`; {LAUNCHER_SESSION_ENV} "
            "is set by the harness this is run under"
        )
    if SESSION_ID.fullmatch(manager) is None:
        raise Refused(
            f"{manager!r} is not the shape a session id has (1-200 characters of letters, "
            "digits, dot, underscore or hyphen, starting with a letter or digit), so nothing "
            f"can carry it as a `{LABEL_LAUNCHER}` label"
        )
    return Own(manager)


#: The options each mode answers on its own, named so a combination that would silently
#: do something other than what was asked is refused rather than ranked. An invocation
#: that both listed and acknowledged, or asked for `--json` and got a receipt, is one a
#: caller reads as having done both.
ALONE: dict[str, tuple[str, ...]] = {
    "--print-surface": (
        "host",
        "own",
        "session",
        "json",
        "no_disk",
        "acknowledge",
        "reason",
        "stop_verdict",
    ),
    "--stop-verdict": ("host", "own", "session", "json", "no_disk", "acknowledge", "reason"),
    "--acknowledge": ("host", "own", "session", "json", "no_disk", "stop_verdict"),
}


def _one_mode(arguments: argparse.Namespace) -> None:
    """Refuse an invocation that names two modes, or a `--reason` with nothing to give it."""
    for flag, exclusive in ALONE.items():
        held = flag == "--print-surface" and arguments.print_surface
        held = held or (flag == "--stop-verdict" and arguments.stop_verdict)
        held = held or (flag == "--acknowledge" and arguments.acknowledge is not None)
        if not held:
            continue
        named = sorted(
            "--" + other.replace("_", "-")
            for other in exclusive
            # An empty `--reason ""` is still a reason named, and never silently dropped.
            if getattr(arguments, other) not in (None, False, [])
        )
        if named:
            raise Refused(
                f"`{flag}` answers on its own and {', '.join(f'`{n}`' for n in named)} "
                f"would be ignored; run `{flag}` by itself, then ask for the rest"
            )
    if arguments.reason is not None and arguments.acknowledge is None:
        raise Refused(
            "`--reason` is the reason an acknowledgement carries, and nothing here is "
            "acknowledging; name `--acknowledge <branch>` beside it"
        )


#: The fields a stop guard's neutral input may carry, each with the type it must have.
#: Read closed, as the verb reads its own: a field it does not name makes the input
#: unreadable rather than ignored.
STOP_INPUT_FIELDS: dict[str, type] = {"session": str, "continuation": bool}


def _stop_session(text: str) -> str:
    """The session a stop guard's neutral input names.

    :class:`Refused` for anything but one object carrying only :data:`STOP_INPUT_FIELDS`,
    and for one naming no session: a verdict about nobody could only permit a stop it was
    never asked about.
    """
    try:
        document = json.loads(text)
    except ValueError as malformed:
        raise Refused(f"the stop input is not one JSON object ({malformed})") from malformed
    if not isinstance(document, dict):
        raise Refused(f"the stop input is a JSON {type(document).__name__}, not an object")
    unknown = sorted(set(document) - set(STOP_INPUT_FIELDS))
    if unknown:
        raise Refused(f"the stop input carries fields a stop guard does not send: {unknown}")
    session = document.get("session")
    if not session:
        raise Refused("the stop input names no session, so there is no one to answer about")
    for field, kind in STOP_INPUT_FIELDS.items():
        # Checked exactly, `null` included: a JSON number is not a continuation.
        if field in document and type(document[field]) is not kind:
            raise Refused(f"the stop input's `{field}` is not a {kind.__name__}")
    return str(session)


class Blocks(TypedDict):
    """The neutral verdict refusing the stop, and the reason the session is shown."""

    verdict: Literal["block"]
    reason: str


class OwesNothing(TypedDict):
    """The neutral verdict saying this source finds nothing owed; the verb decides the stop."""

    verdict: Literal["none"]


def stop_verdict(stdin: Reader | None, err: Writer | None = None) -> Blocks | OwesNothing:
    """The neutral verdict for the stop input on ``stdin``: `block` with the listing, or `none`.

    Never raises for what it was handed: input that could not be read or names no session,
    a refusal, a listing that could not be produced, or one that left out a row it could
    not read is a `block` saying why, because answering `none` would claim nothing is owed
    when that was not established. Unresolved rows go to ``err`` as well, as the listing's
    do, and nothing but the verdict reaches its channel.
    """
    err = sys.stderr if err is None else err
    warnings: list[str] = []
    try:
        if stdin is None:
            # What `sys.stdin` is when the process was started with descriptor 0 closed.
            raise Refused("the stop input could not be read (this process has no standard input)")
        try:
            text = stdin.read()
        except (OSError, UnicodeDecodeError) as unreadable:
            raise Refused(f"the stop input could not be read ({unreadable})") from unreadable
        session = _stop_session(text)
        # Joined to its flag, so a session beginning with `-` reaches the shape check that
        # refuses it rather than being read by the parser as an option of its own.
        arguments = _parser().parse_args([f"--session={session}", "--no-disk"])
        status, lines = _answer(arguments, warnings)
    except (Refused, Unanswered) as failure:
        status, lines = (
            UNANSWERED,
            [
                f"`just unpublished --stop-verdict` could not say which branches this session's "
                f"runs left preserved: {failure}"
            ],
        )
    for warning in warnings:
        print(f"unpublished: {warning}", file=err)
    left_out = [warning for warning in warnings if warning.startswith(ROW_LEFT_OUT)]
    if left_out:
        lines = [
            *lines,
            "`just unpublished --stop-verdict` could not read every branch `onevcs` reported, "
            "so it cannot say this session owes none of the ones it left out:",
            *left_out,
        ]
    elif status == NOTHING_COUNTED:
        return OwesNothing(verdict="none")
    return Blocks(verdict="block", reason="\n".join(lines))


def main(
    argv: Sequence[str] | None = None,
    out: Writer | None = None,
    err: Writer | None = None,
    stdin: Reader | None = None,
) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    arguments = _parser().parse_args(argv)
    warnings: list[str] = []
    try:
        _one_mode(arguments)
        if arguments.print_surface:
            print_surface(out)
            return NOTHING_COUNTED
        if arguments.stop_verdict:
            print(json.dumps(stop_verdict(sys.stdin if stdin is None else stdin, err)), file=out)
            return NOTHING_COUNTED
        status, lines = _answer(arguments, warnings)
    except Refused as refusal:
        status, lines = REFUSED, []
        warnings.append(f"refused: {refusal}")
    except Unanswered as failure:
        status, lines = UNANSWERED, []
        warnings.append(f"could not answer: {failure}")
    # Everything that could not be resolved, and the refusal or failure last, on standard
    # error — where none of it changes the status.
    for warning in warnings:
        print(f"unpublished: {warning}", file=err)
    for line in lines:
        print(line, file=out)
    return status


def _answer(arguments: argparse.Namespace, warnings: list[str]) -> tuple[int, list[str]]:
    """The status and the standard-output lines one invocation answers with."""
    named = os.environ.get(LAUNCHER_SESSION_ENV)
    if arguments.acknowledge is not None:
        session = _session(named)
        receipt, rows = _acknowledge(
            Branch(arguments.acknowledge), arguments.reason, session, warnings
        )
        # The status is the host-level target's, read after the write: this branch no
        # longer counts, and any other preserved branch still does.
        acknowledged = _read_acknowledgements(session, warnings)
        owed = any(counted(row, _standing(row, acknowledged, warnings)) for row in rows)
        return COUNTED if owed else NOTHING_COUNTED, [receipt]
    with_holders = not arguments.no_disk
    match _target(arguments, named):
        case Own(manager=manager):
            # The acknowledgements are the manager's whose branches these are: asked about
            # by `--session`, that session's rather than the shell's.
            session = manager
            rows = _own_rows(manager, with_holders, warnings)
        case target:
            try:
                session = _session(named)
            except Refused as malformed:
                # A listing still answers: with no acknowledgement read, every branch
                # counts, which is the conservative reading, and why is said.
                warnings.append(f"{malformed}; no acknowledgement is read, so every branch counts")
                session = None
            if target is None:
                rows = _host_rows(warnings)
            else:
                rows = _session_rows(target, with_holders, warnings)
    acknowledged = _read_acknowledgements(session, warnings)
    rendered = [
        _rendered(
            row,
            _standing(row, acknowledged, warnings),
            None if arguments.no_disk else _disk(row, warnings),
        )
        for row in rows
    ]
    status = COUNTED if any(row["counted"] for row in rendered) else NOTHING_COUNTED
    if arguments.json:
        return status, [json.dumps(rendered, indent=2)]
    listing = io.StringIO()
    _table(rendered, listing)
    return status, listing.getvalue().splitlines()


if __name__ == "__main__":
    sys.exit(main())
