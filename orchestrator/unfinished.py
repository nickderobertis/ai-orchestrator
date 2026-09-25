"""`just unfinished`: what one manager session still owes before its turn can end.

Two questions, one verdict. **Unwatched**: a run this session launched that nothing is
watching — the installed engine's own `onepipeline unwatched --session <ID>`. **Unpublished**:
a branch a run this session launched left preserved, neither landed nor acknowledged
with a reason — `orchestrator/unpublished.py`'s own-sessions target, `--own --session
<ID> --no-disk`, run in this process. Both are asked about the same manager session:
the `--session` value, else `ONEPIPELINE_LAUNCHER_SESSION` as `scripts/launcher-session.sh`
establishes it, which is the identity both halves are keyed on. Both reads are live
every time; nothing is memoized.

Standard output carries both halves, each under a heading naming the question it
answers; `--json` emits them as one object, `{"unwatched": [<the verb's lines>],
"unpublished": [<the view's rows>]}`. Everything either half could not resolve goes to
standard error, where it changes no status.

**This module is the one declaration of the status vocabulary** (:data:`EXIT_STATUSES`),
which `scripts/unfinished.sh --print-surface` prints. `6` is the engine's own `unwatched`
status, kept as the word for that half, and `7` is `just unpublished`'s counted status;
`tests/test_unfinished.py` reconciles the first against the installed engine over a
built run root and the second against `scripts/unpublished.sh --print-surface`, so
neither number is a copy nothing checks.

Stdlib-only, and importable with `PYTHONPATH` at the checkout root and nothing else, as
`orchestrator/unpublished.py` is: no `uv run`, because `uv` takes an exclusive lock on
the project environment.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from typing import NamedTuple

from orchestrator import unpublished
from orchestrator.root import REPO_ROOT

__all__ = [
    "BOTH",
    "EXIT_STATUSES",
    "NOTHING_OWED",
    "UNANSWERED",
    "UNPUBLISHED",
    "UNWATCHED",
    "main",
    "print_surface",
]

#: Nothing is owed: every run this session launched is watched, and no branch its runs
#: left is counted.
NOTHING_OWED = 0
#: At least one run this session launched is unwatched, and no branch is counted. The
#: engine's own `unwatched` status, kept as the word for that half.
UNWATCHED = 6
#: At least one branch this session's runs left is counted, and no run is unwatched.
#: `just unpublished`'s own counted status.
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


#: The exit-status vocabulary, one entry per status. `scripts/unfinished.sh
#: --print-surface` prints each as `status <code> <name>`.
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

#: How long the engine's `unwatched` is given, so a wedged read answers
#: :data:`UNANSWERED` rather than holding the caller open for ever.
UNWATCHED_TIMEOUT_SECONDS = 60


class Unanswered(Exception):
    """A half that could not answer, with the reason a person reads."""


class Half(NamedTuple):
    """One half's answer: whether it found something owed, and what it reported."""

    owed: bool
    lines: list[str]


def print_surface(out: unpublished.Writer | None = None) -> None:
    """The vocabulary a consumer reads from `scripts/unfinished.sh --print-surface`."""
    out = sys.stdout if out is None else out
    for status in EXIT_STATUSES:
        print(f"status {status.code} {status.name}", file=out)


def _unwatched(session: str, err: unpublished.Writer) -> Half:
    """The engine's `unwatched --session <session>`: `0` nothing, `6` its lines.

    Reached as the installed binary — this checkout's `.venv/bin` first, the search path
    after — and never through `uv run`. Whatever it writes to standard error is passed
    through, where it changes no status.
    """
    binary = unpublished._binary("onepipeline")
    if binary is None:
        raise Unanswered(
            f"found no `onepipeline` to ask, at {REPO_ROOT / '.venv' / 'bin' / 'onepipeline'} "
            "or on the search path; run `just bootstrap` from the checkout root"
        )
    command = [binary, "unwatched", "--session", session]
    try:
        done = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=UNWATCHED_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        raise Unanswered(
            f"`{shlex.join(command)}` ran past its {UNWATCHED_TIMEOUT_SECONDS}s bound"
        ) from expired
    except OSError as failure:
        raise Unanswered(f"could not run `{shlex.join(command)}`: {failure}") from failure
    for line in done.stderr.splitlines():
        print(line, file=err)
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


def _unpublished(session: str, as_json: bool, err: unpublished.Writer) -> Half:
    """The view's own-sessions target for ``session``, in this process: `0` or `7`."""
    arguments = ["--own", "--session", session, "--no-disk", *(["--json"] if as_json else [])]
    out = io.StringIO()
    status = unpublished.main(arguments, out=out, err=err)
    if status == unpublished.NOTHING_COUNTED:
        return Half(owed=False, lines=out.getvalue().splitlines())
    if status == unpublished.COUNTED:
        return Half(owed=True, lines=out.getvalue().splitlines())
    raise Unanswered(
        f"`just unpublished {shlex.join(arguments)}` exited {status}; its standard error is above"
    )


def _session(value: str | None) -> str:
    """The manager session both halves are asked about, or :class:`ValueError` saying why."""
    if not value:
        raise ValueError(
            "no manager session identifies this shell, so there is nothing to ask about: name "
            f"one with `--session <ID>`; {unpublished.LAUNCHER_SESSION_ENV} is set by the "
            "harness this is run under"
        )
    if unpublished.SESSION_ID.fullmatch(value) is None:
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
        help=f"the manager session to ask about; default {unpublished.LAUNCHER_SESSION_ENV}",
    )
    parser.add_argument("--json", action="store_true", help="emit both halves as one object")
    parser.add_argument(
        "--print-surface", action="store_true", help="print the exit-status vocabulary and exit"
    )
    return parser


def _status(unwatched_half: Half, unpublished_half: Half) -> int:
    if unwatched_half.owed and unpublished_half.owed:
        return BOTH
    if unwatched_half.owed:
        return UNWATCHED
    return UNPUBLISHED if unpublished_half.owed else NOTHING_OWED


def main(
    argv: Sequence[str] | None = None,
    out: unpublished.Writer | None = None,
    err: unpublished.Writer | None = None,
) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    arguments = _parser().parse_args(argv)
    if arguments.print_surface:
        if arguments.session is not None or arguments.json:
            print("unfinished: refused: `--print-surface` answers on its own", file=err)
            return REFUSED
        print_surface(out)
        return NOTHING_OWED
    # An explicit `--session` is judged as given, an empty one included: falling back to
    # the environment would answer for a session the caller did not name.
    named = arguments.session
    if named is None:
        named = os.environ.get(unpublished.LAUNCHER_SESSION_ENV)
    try:
        session = _session(named)
    except ValueError as refusal:
        print(f"unfinished: refused: {refusal}", file=err)
        return REFUSED
    halves: dict[str, Half] = {}
    failed = False
    for name in ("unwatched", "unpublished"):
        try:
            halves[name] = (
                _unwatched(session, err)
                if name == "unwatched"
                else _unpublished(session, arguments.json, err)
            )
        except Unanswered as failure:
            print(f"unfinished: the {name} half could not answer: {failure}", file=err)
            failed = True
    if arguments.json:
        if failed:
            return UNANSWERED
        document: dict[str, object] = {
            "unwatched": halves["unwatched"].lines,
            # llmlint: ignore[boundary_inputs_validated] Not a boundary: the text is what
            # `unpublished.main` just wrote to this process's own buffer under `--json`,
            # which is `json.dumps` of a list of `ROW_FIELDS` rows, and a status other than
            # `0` or `7` was already refused as unanswered above.
            "unpublished": json.loads("\n".join(halves["unpublished"].lines)),
        }
        print(json.dumps(document, indent=2), file=out)
        return _status(halves["unwatched"], halves["unpublished"])
    if "unwatched" in halves:
        print(UNWATCHED_HEADING, file=out)
        for line in halves["unwatched"].lines or [EVERY_RUN_WATCHED]:
            print(line, file=out)
    if "unpublished" in halves:
        if "unwatched" in halves:
            print(file=out)
        print(UNPUBLISHED_HEADING, file=out)
        for line in halves["unpublished"].lines:
            print(line, file=out)
    if failed:
        return UNANSWERED
    return _status(halves["unwatched"], halves["unpublished"])


if __name__ == "__main__":
    sys.exit(main())
