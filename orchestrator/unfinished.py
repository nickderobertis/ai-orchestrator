"""`just unfinished`: what one manager session still owes before its turn can end.

Two questions, one verdict, and both are the engine's to answer. **Unwatched**: a run this
session launched that nothing is watching — the installed engine's own `onepipeline
unwatched --session <ID>`. **Unpublished**: a branch a run this session launched left
preserved, neither landed nor acknowledged with a reason — the engine's own `onepipeline
unpublished --session <ID> --format json`, under the acknowledgement directory and drafter
graph `just unpublished` and the `Stop` hook name (:func:`acknowledgements` and
:data:`PR_AUTHOR_GRAPH`, which `tests/test_unfinished.py` holds to both). This module
decides neither: it reads each verb's exit status and passes on what it
wrote. Both are asked about the same manager session: the `--session` value, else
`ONEPIPELINE_LAUNCHER_SESSION` as `scripts/launcher-session.sh` establishes it. Both reads
are live every time; nothing is memoized.

Standard output carries both halves, each under a heading naming the question it
answers; `--json` emits them as one object, `{"unwatched": [<the verb's lines>],
"unpublished": <the engine's document>}`, the document being `onepipeline unpublished
--format json`'s whole: `{target, verdict, rows, unresolved}`. Everything either half could
not resolve goes to standard error, where it changes no status.

**This module is the one declaration of the status vocabulary** (:data:`EXIT_STATUSES`).
`6` is the engine's own `unwatched` status and `7` the engine's own `unpublished` status for
a counted branch, each kept as the word for its half; `tests/test_unfinished.py` reconciles
the first against the installed engine over a built run root and the second against the
exit statuses onepipeline's contract declares at the pinned release, so neither number is a
copy nothing checks.

Stdlib-only, and importable with `PYTHONPATH` at the checkout root and nothing else: no
`uv run`, because `uv` takes an exclusive lock on the project environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple, NewType, Protocol

from orchestrator import npm_installs, spike_brief
from orchestrator.root import REPO_ROOT

__all__ = [
    "BOTH",
    "EXIT_STATUSES",
    "NOTHING_OWED",
    "UNANSWERED",
    "UNPUBLISHED",
    "UNWATCHED",
    "main",
]

#: Nothing is owed: every run this session launched is watched, and no branch its runs
#: left is counted.
NOTHING_OWED = 0
#: At least one run this session launched is unwatched, and no branch is counted. The
#: engine's own `unwatched` status, kept as the word for that half.
UNWATCHED = 6
#: At least one branch this session's runs left is counted, and no run is unwatched. The
#: engine's own `unpublished` status for a counted branch.
UNPUBLISHED = 7
#: Both: a run is unwatched and a branch is counted.
BOTH = 8
#: The invocation was refused: no manager session to ask about, or one of another shape.
REFUSED = 2
#: A half could not answer, and standard error says why. Never `0`, because nothing
#: owed would not be known.
UNANSWERED = 1


class Status(NamedTuple):
    """One exit status: the number a consumer branches on, its name, and what it says."""

    code: int
    name: str
    phrase: str


#: The exit-status vocabulary, one entry per status.
EXIT_STATUSES: tuple[Status, ...] = (
    Status(NOTHING_OWED, "nothing-owed", "no run is unwatched and no branch is counted"),
    Status(UNWATCHED, "unwatched", "at least one owned run is unwatched; no branch is counted"),
    Status(UNPUBLISHED, "unpublished", "at least one branch is counted; no run is unwatched"),
    Status(BOTH, "both", "an owned run is unwatched and a branch is counted"),
    Status(REFUSED, "refused", "the invocation was refused"),
    Status(UNANSWERED, "unanswered", "a half could not answer; standard error says why"),
)

UNWATCHED_HEADING = "== unwatched: runs this session launched that nothing is watching =="
UNPUBLISHED_HEADING = (
    "== unpublished: branches this session's runs left preserved, not landed or acknowledged =="
)
EVERY_RUN_WATCHED = "no run this session launched is unwatched"

#: The environment variable `scripts/launcher-session.sh` exports the manager session as.
LAUNCHER_SESSION_ENV = "ONEPIPELINE_LAUNCHER_SESSION"
#: The shape a session id has, as `scripts/launcher-session.sh` judges it.
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")

#: Where this host keeps a manager session's acknowledgements, under the state home: the
#: directory `just unpublished` and the `Stop` hook both hand the engine.
ACKNOWLEDGEMENTS_UNDER = ("ai-orchestrator", "stop-unfinished", "acknowledged")
#: The drafter graph every landing command the engine prints runs.
PR_AUTHOR_GRAPH = REPO_ROOT / "graphs" / "pr-author.yaml"

#: How long each half is given, so a wedged read answers :data:`UNANSWERED` rather than
#: holding the caller open for ever.
HALF_TIMEOUT_SECONDS = 60


class Writer(Protocol):
    """Where this module writes: the stream a caller hands it, or the process's own."""

    def write(self, text: str, /) -> object:
        """Take ``text``; what a stream answers with is its own business."""


class Unanswered(Exception):
    """A half that could not answer, with the reason a person reads."""


class Half(NamedTuple):
    """One half's answer: whether it found something owed, and what it reported."""

    owed: bool
    lines: list[str]


class Unpublished(NamedTuple):
    """The unpublished half's answer, beside the engine's own document it was read from."""

    half: Half
    document: object


def _run(command: list[str], err: Writer) -> subprocess.CompletedProcess[str]:
    """Run one half's command, passing its standard error through."""
    try:
        done = subprocess.run(  # noqa: S603 - an argv this module composes, never a shell
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=HALF_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        raise Unanswered(
            f"`{shlex.join(command)}` ran past its {HALF_TIMEOUT_SECONDS}s bound"
        ) from expired
    except OSError as failure:
        raise Unanswered(f"could not run `{shlex.join(command)}`: {failure}") from failure
    for line in done.stderr.splitlines():
        print(line, file=err)
    return done


def acknowledgements() -> Path:
    """The acknowledgement directory, resolved as the recipe's and the hook's shell does.

    `${XDG_STATE_HOME:-$HOME/.local/state}`: an unset or empty state home falls back.
    """
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state).joinpath(*ACKNOWLEDGEMENTS_UNDER)


def _engine() -> str:
    """This checkout's npm-installed engine, never whichever the search path offers."""
    if not os.access(npm_installs.ENGINE, os.X_OK):
        raise Unanswered(
            f"found no `onepipeline` to ask at {npm_installs.ENGINE}, where this checkout's "
            "bun.lock installs it; run `just bootstrap` from the checkout root"
        )
    return str(npm_installs.ENGINE)


def _unwatched(session: str, err: Writer) -> Half:
    """The engine's `unwatched --session <session>`: `0` nothing, `6` its lines.

    Reached as the installed binary and never through `uv run`.
    """
    command = [_engine(), "unwatched", "--session", session]
    done = _run(command, err)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if done.returncode == NOTHING_OWED:
        return Half(owed=False, lines=lines)
    if done.returncode == UNWATCHED and lines:
        return Half(owed=True, lines=lines)
    if done.returncode == UNWATCHED:
        raise Unanswered(f"`{shlex.join(command)}` exited {UNWATCHED} naming no run")
    raise Unanswered(
        f"`{shlex.join(command)}` exited {done.returncode}, a status outside its vocabulary "
        f"({NOTHING_OWED} or {UNWATCHED}); its standard error is above"
    )


def _unpublished(session: str, err: Writer) -> Unpublished:
    """The engine's `unpublished --session <session> --format json`: `0` or `7`, and its
    document. Any other status — `1` unanswered, `2` refused — is this half not answering.
    """
    command = [
        _engine(),
        "unpublished",
        "--session",
        session,
        "--format",
        "json",
        "--acknowledgements",
        str(acknowledgements()),
        "--pr-author-graph",
        str(PR_AUTHOR_GRAPH),
    ]
    done = _run(command, err)
    if done.returncode not in (NOTHING_OWED, UNPUBLISHED):
        raise Unanswered(
            f"`{shlex.join(command)}` exited {done.returncode}; its standard error is above"
        )
    try:
        document: object = json.loads(done.stdout)
    except json.JSONDecodeError as failure:
        raise Unanswered(
            f"`{shlex.join(command)}` exited {done.returncode} without the one JSON document "
            f"it writes: {failure}"
        ) from failure
    match document:
        case {"rows": list(rows)}:
            lines = [_row(row, command).line() for row in rows]
        case _:
            raise Unanswered(f"`{shlex.join(command)}` wrote a document with no `rows` list")
    return Unpublished(
        half=Half(owed=done.returncode == UNPUBLISHED, lines=lines), document=document
    )


#: A branch name as the engine's row names it.
Branch = NewType("Branch", str)
#: A repository identity as the engine's row names it.
Identity = NewType("Identity", str)


class Row(NamedTuple):
    """The fields of one of the engine's rows this module renders, read whole or not at all."""

    branch: Branch
    identity: Identity
    counted: bool
    in_flight: bool
    land_command: tuple[str, ...]
    reason: str | None

    def line(self) -> str:
        """The row as one line: its branch, and what the engine decided about it."""
        where = f"{self.branch} [{self.identity}]"
        if self.counted and self.land_command:
            return f"{where} counted; land it: {shlex.join(self.land_command)}"
        if self.counted:
            return f"{where} counted"
        if self.in_flight:
            return f"{where} in flight"
        return f"{where} acknowledged: {self.reason}"


def _row(value: object, command: list[str]) -> Row:
    """One row of the engine's document, or :class:`Unanswered` naming what was malformed.

    A row this module cannot read whole is the half not answering rather than a line that
    misstates it: an uncounted row is in flight or acknowledged with a reason, and nothing
    else.
    """
    match value:
        case {
            "branch": str(branch),
            "identity": str(identity),
            "counted": bool(counted),
            "in_flight": bool(in_flight),
            "land_command": list(words),
            "acknowledgement": acknowledgement,
        } if all(isinstance(word, str) for word in words):
            pass
        case _:
            raise Unanswered(f"`{shlex.join(command)}` wrote a row it could not read: {value!r}")
    reason: str | None
    match acknowledgement:
        case {"reason": str(stated)}:
            reason = stated
        case None:
            reason = None
        case _:
            raise Unanswered(
                f"`{shlex.join(command)}` wrote an acknowledgement it could not read: {value!r}"
            )
    if not counted and not in_flight and reason is None:
        raise Unanswered(
            f"`{shlex.join(command)}` wrote a row neither counted, in flight nor "
            f"acknowledged: {value!r}"
        )
    return Row(Branch(branch), Identity(identity), counted, in_flight, tuple(words), reason)


def _session(value: str | None) -> str:
    """The manager session both halves are asked about, or :class:`ValueError` saying why."""
    if not value:
        raise ValueError(
            "no manager session identifies this shell, so there is nothing to ask about: name "
            f"one with `--session <ID>`; {LAUNCHER_SESSION_ENV} is set by the harness this is "
            "run under"
        )
    if SESSION_ID.fullmatch(value) is None:
        raise ValueError(
            f"{value!r} is not the shape a session id has (1-200 characters of letters, digits, "
            "dot, underscore or hyphen, starting with a letter or digit)"
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="just unfinished",
        description="What this manager session still owes: runs nothing is watching, and "
        "branches its runs left preserved that are neither landed nor acknowledged.",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        help=f"the manager session to ask about; default {LAUNCHER_SESSION_ENV}",
    )
    parser.add_argument("--json", action="store_true", help="emit both halves as one object")
    return parser


def _status(unwatched_half: Half, unpublished_half: Half) -> int:
    if unwatched_half.owed and unpublished_half.owed:
        return BOTH
    if unwatched_half.owed:
        return UNWATCHED
    return UNPUBLISHED if unpublished_half.owed else NOTHING_OWED


def main(
    argv: Sequence[str] | None = None,
    out: Writer | None = None,
    err: Writer | None = None,
) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    arguments = _parser().parse_args(argv)
    # An explicit `--session` is judged as given, an empty one included: falling back to
    # the environment would answer for a session the caller did not name.
    named = arguments.session
    if named is None:
        named = os.environ.get(LAUNCHER_SESSION_ENV)
    try:
        session = _session(named)
    except ValueError as refusal:
        print(f"unfinished: refused: {refusal}", file=err)
        return REFUSED
    unwatched_half: Half | None = None
    unpublished_half: Half | None = None
    document: object = None
    try:
        unwatched_half = _unwatched(session, err)
    except Unanswered as failure:
        print(f"unfinished: the unwatched half could not answer: {failure}", file=err)
    try:
        answer = _unpublished(session, err)
        unpublished_half, document = answer.half, answer.document
    except Unanswered as failure:
        print(f"unfinished: the unpublished half could not answer: {failure}", file=err)
    brief_items, brief_diagnostics = spike_brief.items(session)
    for diagnostic in brief_diagnostics:
        print(diagnostic, file=err)
    if arguments.json:
        if unwatched_half is None or unpublished_half is None:
            return UNANSWERED
        combined: dict[str, object] = {
            "unwatched": unwatched_half.lines,
            "unpublished": document,
        }
        if brief_items:
            combined["spike_briefs"] = brief_items
        print(json.dumps(combined, indent=2), file=out)
        return _status(unwatched_half, unpublished_half)
    if unwatched_half is not None:
        print(UNWATCHED_HEADING, file=out)
        for line in unwatched_half.lines or [EVERY_RUN_WATCHED]:
            print(line, file=out)
    if unpublished_half is not None:
        if unwatched_half is not None:
            print(file=out)
        print(UNPUBLISHED_HEADING, file=out)
        for line in unpublished_half.lines or ["no preserved branch of this session's runs"]:
            print(line, file=out)
        if unpublished_half.owed:
            print(
                "acknowledge one: just unpublished --acknowledge <branch> --reason "
                '"<why it is deliberately left>"',
                file=out,
            )
    if brief_items:
        print("\n== spike briefs: optional user approval (advisory) ==", file=out)
        for brief_item in brief_items:
            print(
                f"{brief_item['plan_project']}: {brief_item['url']} "
                f"({brief_item['approve_command']})",
                file=out,
            )
    if unwatched_half is None or unpublished_half is None:
        return UNANSWERED
    return _status(unwatched_half, unpublished_half)


if __name__ == "__main__":
    sys.exit(main())
