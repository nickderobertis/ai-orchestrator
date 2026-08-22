#!/usr/bin/env python3
"""The remote host's decisioning, as the program `onevcs` runs for `gh`.

`ONEVCS_GH` is the seam `onevcs` publishes for exactly this: it names the program
every `gh` call goes through, and `onevcs`'s own suite substitutes it the same way.
Nothing else about a publication is substituted here — the branch is pushed with real
git into a real bare origin — judged by a real `pre-push` hook where the journey asks
for one — and the change request this records is opened by the real `onevcs` from the
real recipe.

What it answers is the four calls opening a change request makes: who is
authenticated, which changes already exist for a head and base, the `pr create`
itself, and the head commit of what was just created. Every call is appended to
`FAKE_GH_CALLS` as JSON argv, and the body of each opened change request is written
to `FAKE_GH_STATE/pr-<n>.body` — byte for byte, because "the change request carries
exactly the drafted body" is a claim about characters and an assertion against
anything but the recorded file would be a claim about this program instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

#: Where each call's argv is appended, one JSON list per line.
CALLS_ENV = "FAKE_GH_CALLS"
#: The directory holding what this host remembers between calls.
STATE_ENV = "FAKE_GH_STATE"
#: The bare origin a created change request reads its head commit from, so the sha it
#: reports is the one real git actually put there.
ORIGIN_ENV = "FAKE_GH_ORIGIN"
#: The repository every URL this host mints is under.
REPOSITORY = "acme-corp/hosted"


def _state() -> Path:
    state = Path(os.environ[STATE_ENV])
    state.mkdir(parents=True, exist_ok=True)
    return state


def _record(argv: list[str]) -> None:
    with Path(os.environ[CALLS_ENV]).open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(argv) + "\n")


class Call(NamedTuple):
    """The `--name value` pairs of one call, named rather than looked up by string.

    `gh` is given nothing else here, and every option the four answered calls carry has
    a field: a name this host does not answer to is read and dropped, which is what
    keeps an unrecognized option from reaching a record as a key nothing reads back.
    """

    #: `--head`, the branch a change request is opened from.
    head: str = ""
    #: `--base`, the branch it is opened onto.
    base: str = ""
    #: `--title`, the subject the publication composed.
    title: str = ""
    #: `--body`, the drafted prose under test.
    body: str = ""
    #: `--json`, split into the field names the caller asked `pr view` for.
    fields: tuple[str, ...] = ()


class Change(TypedDict):
    """What this host remembers about one opened change request, between calls.

    The field names are `gh`'s own, `headRefOid` included, because `onevcs` reads this
    back as `gh`'s answer and a name of this program's choosing would prove nothing
    about the real call.
    """

    number: int
    url: str
    state: str
    headRefOid: str
    head: str
    base: str
    title: str


def _call(argv: list[str]) -> Call:
    """One invocation's options, read off the argv `onevcs` really passed."""
    read: dict[str, str] = {}
    index = 0
    while index < len(argv):
        if argv[index].startswith("--") and index + 1 < len(argv):
            read[argv[index][2:]] = argv[index + 1]
            index += 2
            continue
        index += 1
    return Call(
        head=read.get("head", ""),
        base=read.get("base", ""),
        title=read.get("title", ""),
        body=read.get("body", ""),
        fields=tuple(field for field in read.get("json", "").split(",") if field),
    )


def _remembered() -> list[Change]:
    """Every change request this host has opened, in the order it opened them."""
    # `cast` rather than a check: every one of these files was written by `_create`
    # below, so the shape is this program's own and a JSON round trip is the only reason
    # it is not statically known. A validator here would be asserting against itself.
    return [
        cast(Change, json.loads(record.read_text(encoding="utf-8")))
        for record in sorted(_state().glob("pr-*.json"))
    ]


def _head_sha(head: str) -> str:
    """What the origin really has on that branch, read from the origin itself."""
    origin = os.environ.get(ORIGIN_ENV, "")
    if not origin:
        return "unknown"
    done = subprocess.run(
        ["git", "--git-dir", origin, "rev-parse", f"refs/heads/{head}"],
        text=True,
        capture_output=True,
        check=False,
    )
    return done.stdout.strip() or "unknown"


def _create(call: Call) -> int:
    state = _state()
    number = 1
    while (state / f"pr-{number}.json").exists():
        number += 1
    (state / f"pr-{number}.body").write_text(call.body, encoding="utf-8")
    opened: Change = {
        "number": number,
        "url": f"https://github.com/{REPOSITORY}/pull/{number}",
        "state": "OPEN",
        "headRefOid": _head_sha(call.head),
        "head": call.head,
        "base": call.base,
        "title": call.title,
    }
    (state / f"pr-{number}.json").write_text(json.dumps(opened), encoding="utf-8")
    sys.stdout.write(f"https://github.com/{REPOSITORY}/pull/{number}\n")
    return 0


def _view(number: str, call: Call) -> int:
    """Exactly the fields the call asked for, as `gh` answers."""
    # `cast` for the same reason as in `_remembered`: `_create` wrote this file.
    record = cast(Change, json.loads((_state() / f"pr-{number}.json").read_text(encoding="utf-8")))
    # Projected off a plain mapping of the record, because which fields `--json` names is
    # the caller's to choose and a fixed field cannot stand in for a chosen one.
    remembered = dict(record)
    sys.stdout.write(json.dumps({field: remembered.get(field) for field in call.fields}) + "\n")
    return 0


def _list(call: Call) -> int:
    """Every open change request for that head and base — none, until one is created."""
    matched = [
        {
            "number": change["number"],
            "url": change["url"],
            "state": change["state"],
            "headRefOid": change["headRefOid"],
        }
        for change in _remembered()
        if change["head"] == call.head and change["base"] == call.base
    ]
    sys.stdout.write(json.dumps(matched) + "\n")
    return 0


def main(argv: list[str]) -> int:
    _record(argv)
    call = _call(argv)
    match argv:
        case ["api", "user", *_]:
            sys.stdout.write("tester\n")
            return 0
        case ["pr", "create", *_]:
            return _create(call)
        case ["pr", "list", *_]:
            return _list(call)
        case ["pr", "view", number, *_]:
            return _view(number, call)
    sys.stderr.write(f"fake gh: nothing here answers {argv}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
