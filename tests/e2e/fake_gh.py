#!/usr/bin/env python3
"""The remote host's decisioning, as the program `onevcs` runs for `gh`.

`ONEVCS_GH` is the seam `onevcs` publishes for exactly this: it names the program
every `gh` call goes through, and `onevcs`'s own suite substitutes it the same way.
Nothing else about a publication is substituted here — the branch is pushed with real
git into a real bare origin — judged by a real `pre-push` hook where the journey asks
for one — and the change request this records is opened by the real `onevcs` from the
real recipe.

What it answers is the calls opening a change request makes: who is authenticated,
which changes already exist for a head and base, the `pr create` itself, the head
commit of what was just created, whether it is a draft, which checks its base
requires, and the `pr ready` that lifts one. The base is unprotected unless
`FAKE_GH_STATE/checks.json` scripts a watch.
That file contains one `statusCheckRollup` list per poll, with the last repeated;
classic protection then requires `aggregate`. The draft journeys model a workflow
whose aggregate waits on other jobs and which has no `ready_for_review` trigger:
lifting changes only the draft flag and never starts another run. Workflow-runs
and check-suites reads return GitHub's empty-list shapes for that idle head.
Every call is appended to
`FAKE_GH_CALLS` as JSON argv, and the body of each opened change request is written
to `FAKE_GH_STATE/pr-<n>.body` — byte for byte, because "the change request carries
exactly the drafted body" is a claim about characters and an assertion against
anything but the recorded file would be a claim about this program instead.

**A journey may give the base required checks, and decide when the host merges.** Named
by `FAKE_GH_CHECKS`, a file of one check a line — `<name> <status> <conclusion>
<required|advisory>`, `<status>` being `queued`, `in_progress` or `completed` and
`<conclusion>` `-` until it is completed — is what the base's ruleset requires (its
`required` lines) and what every change request's `statusCheckRollup` reports, re-read at
each call, so a journey moves a check by rewriting the file. `pr merge --squash --auto`
then arms the change rather than merging it, and the host merges it — squashing the head's
tree onto the base in the bare origin, as GitHub does, so the merge commit it reports is
one real git put there — at the first call that finds `FAKE_GH_STATE/merge` written.
Unset, the host is the unprotected one above, or the `checks.json` one when that is
scripted instead; the two scripts are never combined in one journey.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict, cast, get_args

#: Where each call's argv is appended, one JSON list per line.
CALLS_ENV = "FAKE_GH_CALLS"
#: The directory holding what this host remembers between calls.
STATE_ENV = "FAKE_GH_STATE"
#: The bare origin a created change request reads its head commit from, so the sha it
#: reports is the one real git actually put there.
ORIGIN_ENV = "FAKE_GH_ORIGIN"
#: The repository every URL this host mints is under.
REPOSITORY = "acme-corp/hosted"
#: The check script the paragraph above describes; unset, nothing is required.
CHECKS_ENV = "FAKE_GH_CHECKS"
#: The file under the state directory a journey writes to let the host merge what it armed.
MERGE_PERMIT = "merge"
#: `startedAt` for every check that has started while the change is a draft, and for the
#: run a workflow on `ready_for_review` starts once it is lifted: a later run, which is how
#: `onevcs` tells a run attached after a lift from the draft's.
CHECK_STARTED = "2026-01-01T00:00:00Z"
CHECK_RERUN_STARTED = "2026-01-01T00:05:00Z"


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] These restate `gh`'s answers
# because this file is the `gh` double; the real `onevcs` parsing them is the check, since a
# shape it no longer reads fails the journey. GitHub's own schema needs a credential no test uses.
CheckRun = TypedDict(
    "CheckRun",
    {"__typename": Literal["CheckRun"], "name": str, "status": str, "conclusion": str | None},
)


class Poll(TypedDict):
    """The host's observation of a check poll, used to prove when a draft was lifted."""

    time: float
    draft: bool
    checks: list[CheckRun]


class MergeCommit(TypedDict):
    """The commit GitHub names when it reports a merged change."""

    oid: str


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def _state() -> Path:
    state = Path(os.environ[STATE_ENV])
    state.mkdir(parents=True, exist_ok=True)
    return state


def _record(argv: list[str]) -> None:
    with Path(os.environ[CALLS_ENV]).open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(argv) + "\n")


class Call(NamedTuple):
    """The `--name value` pairs of one call, named rather than looked up by string.

    `gh` is given nothing else here, and every valued option the answered calls carry
    has a field — `--draft`, the one bare flag among them, is read off the argv
    instead: a name this host does not answer to is read and dropped, which is what
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
    #: Whether the host is holding it as a draft: opened as one when `pr create` was
    #: given `--draft`, and lifted by `pr ready`, which is the one way back out.
    isDraft: bool
    head: str
    base: str
    title: str
    #: Whether `pr merge --auto` armed it, for the host to merge once it may.
    armed: bool
    #: The commit the host's squash put on the base, once it merged.
    mergeCommit: MergeCommit | None
    mergedAt: str | None


#: Where a check run stands, in the words the check script and GitHub's API use.
CheckStatus = Literal["queued", "in_progress", "completed"]
#: How a completed check run concluded, as GitHub's API words it, or `-` before then.
CheckConclusion = Literal[
    "-", "success", "failure", "neutral", "cancelled", "skipped", "timed_out", "stale"
]


class Check(NamedTuple):
    """One line of the check script, as the host reports it."""

    name: str
    status: CheckStatus
    conclusion: CheckConclusion
    required: bool

    def rollup(self, *, lifted: bool) -> dict[str, str]:
        """The check as `gh pr view --json statusCheckRollup` reports it.

        `conclusion` is absent rather than null until the check completes, which is what
        the real rollup does and what `onevcs` reads as "not decided yet".
        """
        entry = {"__typename": "CheckRun", "name": self.name, "status": self.status.upper()}
        if self.status != "queued":
            entry["startedAt"] = CHECK_RERUN_STARTED if lifted else CHECK_STARTED
        if self.status == "completed":
            entry["conclusion"] = self.conclusion.upper()
        return entry


def _checks() -> list[Check]:
    """The check script, read now; none when the journey named none."""
    named = os.environ.get(CHECKS_ENV, "")
    if not named:
        return []
    checks = []
    for line in Path(named).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        name, status, conclusion, blocks = line.split()
        assert status in get_args(CheckStatus), line
        assert conclusion in get_args(CheckConclusion), line
        assert (conclusion == "-") == (status != "completed"), line
        assert blocks in ("required", "advisory"), line
        # Each word was checked against its alias just above; `split` alone loses the type.
        checks.append(
            Check(
                name,
                cast(CheckStatus, status),
                cast(CheckConclusion, conclusion),
                blocks == "required",
            )
        )
    return checks


def _call(argv: list[str]) -> Call:
    """One invocation's options, read off the argv `onevcs` really passed."""
    read: dict[str, str] = {}
    index = 0
    while index < len(argv):
        if argv[index] == "--draft":
            index += 1
            continue
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


# llmlint: ignore-block[e2e_not_mocked] This file is the `gh` boundary AGENTS.md permits doubling — the published CLI `onevcs` delegates to, through the `ONEVCS_GH` seam it publishes for this — and what it adds answers the calls the pinned `onevcs` now makes there, so `onevcs`, the recipes and git stay real.  # noqa: E501 - a directive is one line
def _create(call: Call, *, draft: bool) -> int:
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
        "isDraft": draft,
        "head": call.head,
        "base": call.base,
        "title": call.title,
        "armed": False,
        "mergeCommit": None,
        "mergedAt": None,
    }
    (state / f"pr-{number}.json").write_text(json.dumps(opened), encoding="utf-8")
    sys.stdout.write(f"https://github.com/{REPOSITORY}/pull/{number}\n")
    return 0


# llmlint: ignore-end[e2e_not_mocked]


# llmlint: ignore-block[e2e_not_mocked] This file is the `gh` boundary AGENTS.md permits doubling — the published CLI `onevcs` delegates to, through the `ONEVCS_GH` seam it publishes for this — and the checks, mergeability and merge it reports are the host's own answers there, so `onevcs`, the recipes and git stay real.  # noqa: E501 - a directive is one line
def _view(number: str, call: Call) -> int:
    """Exactly the fields the call asked for, as `gh` answers."""
    path = _state() / f"pr-{number}.json"
    # `cast` for the same reason as in `_remembered`: `_create` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    if record["armed"] and record["state"] == "OPEN" and (_state() / MERGE_PERMIT).exists():
        record = _merge_now(path, record)
    # Projected off a plain mapping of the record, because which fields `--json` names is
    # the caller's to choose and a fixed field cannot stand in for a chosen one.
    remembered: dict[str, object] = {
        **record,
        "statusCheckRollup": [check.rollup(lifted=not record["isDraft"]) for check in _checks()],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
    }
    script = _state() / "checks.json"
    if script.exists() and "statusCheckRollup" in call.fields:
        # The journey writes these typed fixtures; only their JSON round trip loses the type.
        stages = cast(list[list[CheckRun]], json.loads(script.read_text(encoding="utf-8")))
        counter = _state() / "poll"
        poll = int(counter.read_text()) if counter.exists() else 0
        remembered["statusCheckRollup"] = stages[min(poll, len(stages) - 1)]
        counter.write_text(str(poll + 1), encoding="utf-8")
        observation: Poll = {
            "time": time.monotonic(),
            "draft": record["isDraft"],
            "checks": stages[min(poll, len(stages) - 1)],
        }
        with (_state() / "polls.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(observation) + "\n")
    sys.stdout.write(json.dumps({field: remembered.get(field) for field in call.fields}) + "\n")
    return 0


# llmlint: ignore-end[e2e_not_mocked]


# llmlint: ignore-block[e2e_not_mocked] This file is the `gh` boundary AGENTS.md permits doubling — the published CLI `onevcs` delegates to, through the `ONEVCS_GH` seam it publishes for this — and what it adds answers the calls the pinned `onevcs` now makes there, so `onevcs`, the recipes and git stay real.  # noqa: E501 - a directive is one line
def _ready(number: str) -> int:
    """Lift a draft, as `gh pr ready` does: the record says so from then on."""
    path = _state() / f"pr-{number}.json"
    # `cast` for the same reason as in `_remembered`: `_create` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    record["isDraft"] = False
    if (_state() / "checks.json").exists():
        (_state() / "ready-poll").write_text(
            (_state() / "poll").read_text(encoding="utf-8"), encoding="utf-8"
        )
    path.write_text(json.dumps(record), encoding="utf-8")
    return 0


# llmlint: ignore-end[e2e_not_mocked]


# llmlint: ignore-block[e2e_not_mocked] This file is the `gh` boundary AGENTS.md permits doubling — the published CLI `onevcs` delegates to, through the `ONEVCS_GH` seam it publishes for this — and the merge it performs is the host's own, done with real git in the bare origin, so `onevcs`, the recipes and git stay real.  # noqa: E501 - a directive is one line
def _merge(number: str, *, auto: bool) -> int:
    """`pr merge --squash [--auto]`: merge now when the host may, otherwise arm it."""
    path = _state() / f"pr-{number}.json"
    # `cast` for the same reason as in `_remembered`: `_create` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    if record["state"] != "OPEN":
        return 0
    if auto and not (_state() / MERGE_PERMIT).exists():
        record["armed"] = True
        path.write_text(json.dumps(record), encoding="utf-8")
        return 0
    _merge_now(path, record)
    return 0


def _merge_now(path: Path, record: Change) -> Change:
    """Squash the head's tree onto the base in the bare origin, and record the merge."""
    origin = os.environ[ORIGIN_ENV]

    def plumbing(*args: str) -> str:
        return subprocess.run(
            ["git", "--git-dir", origin, *args], text=True, capture_output=True, check=True
        ).stdout.strip()

    base = plumbing("rev-parse", f"refs/heads/{record['base']}")
    tree = plumbing("rev-parse", f"refs/heads/{record['head']}^{{tree}}")
    squashed = plumbing(
        "-c",
        "user.name=fake-host",
        "-c",
        "user.email=host@example.com",
        "commit-tree",
        tree,
        "-p",
        base,
        "-m",
        f"{record['title']} (#{record['number']})",
    )
    plumbing("update-ref", f"refs/heads/{record['base']}", squashed, base)
    record["state"] = "MERGED"
    record["armed"] = False
    record["mergeCommit"] = {"oid": squashed}
    record["mergedAt"] = "2026-01-01T00:10:00Z"
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


def _pr_checks(argv: list[str]) -> int:
    """`pr checks N [--required] --json …`: which checks block the merge, or where each ran.

    A base requiring nothing is answered as `gh` answers it — exit 1, nothing on stdout,
    and the line `onevcs` reads as that answer.
    """
    checks = _checks()
    if "--required" in argv:
        required = [{"name": check.name} for check in checks if check.required]
        if not required:
            sys.stderr.write("no required checks reported on the head branch\n")
            return 1
        sys.stdout.write(json.dumps(required) + "\n")
        return 0
    links = [
        {"name": check.name, "link": f"https://github.com/{REPOSITORY}/actions/runs/1/job/{index}"}
        for index, check in enumerate(checks, start=1)
    ]
    sys.stdout.write(json.dumps(links) + "\n")
    return 0


def _rules() -> int:
    """The base's ruleset: the script's required checks, or no rule at all."""
    required = sorted({check.name for check in _checks() if check.required})
    rules = (
        [
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": name} for name in required]},
            }
        ]
        if required
        else []
    )
    sys.stdout.write(json.dumps(rules) + "\n")
    return 0


# llmlint: ignore-end[e2e_not_mocked]


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


# llmlint: ignore-block[e2e_not_mocked] This file is the `gh` boundary AGENTS.md permits doubling — the published CLI `onevcs` delegates to, through the `ONEVCS_GH` seam it publishes for this — and what it adds answers the calls the pinned `onevcs` now makes there, so `onevcs`, the recipes and git stay real.  # noqa: E501 - a directive is one line
def main(argv: list[str]) -> int:
    _record(argv)
    call = _call(argv)
    match argv:
        case ["api", "user", *_]:
            sys.stdout.write("tester\n")
            return 0
        case ["api", path, *_] if path.startswith(f"repos/{REPOSITORY}/rules/branches/"):
            # Unscripted, no ruleset applies to the base, so none of its rules requires a check.
            return _rules()
        case ["api", path, *_] if path.startswith(
            f"repos/{REPOSITORY}/branches/"
        ) and path.endswith("/protection/required_status_checks"):
            if (_state() / "checks.json").exists():
                sys.stdout.write(json.dumps({"contexts": ["aggregate"], "checks": []}) + "\n")
                return 0
            # GitHub's own 404 for a branch with no classic protection, which is an answer.
            sys.stderr.write("gh: Branch not protected (HTTP 404)\n")
            return 1
        case ["api", path, *_] if "/actions/runs?" in path:
            sys.stdout.write('{"total_count": 0, "workflow_runs": []}\n')
            return 0
        case ["api", path, *_] if "/check-suites?" in path:
            sys.stdout.write('{"total_count": 0, "check_suites": []}\n')
            return 0
        case ["pr", "checks", *_] if "--required" in argv and (_state() / "checks.json").exists():
            sys.stdout.write('[ {"name": "aggregate"} ]\n')
            return 0
        case ["pr", "merge", number, *_] if (_state() / "checks.json").exists():
            record_path = _state() / f"pr-{number}.json"
            # `_create` wrote this Change; deserialization alone loses its type.
            record = cast(Change, json.loads(record_path.read_text(encoding="utf-8")))
            if record["isDraft"]:
                sys.stderr.write("cannot merge a draft\n")
                return 1
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    os.environ[ORIGIN_ENV],
                    "update-ref",
                    f"refs/heads/{record['base']}",
                    record["headRefOid"],
                ],
                check=True,
            )
            record["state"] = "MERGED"
            record["mergeCommit"] = {"oid": record["headRefOid"]}
            record_path.write_text(json.dumps(record), encoding="utf-8")
            return 0
        case ["pr", "create", *_]:
            return _create(call, draft="--draft" in argv)
        case ["pr", "list", *_]:
            return _list(call)
        case ["pr", "view", number, *_]:
            return _view(number, call)
        case ["pr", "ready", number, *_]:
            return _ready(number)
        case ["run", "view", *_] if "--log" in argv:
            # The log `onevcs` keeps beside a check it records; its content is not this
            # host's to decide, only that the host produced one.
            sys.stdout.write(f"log of {argv[argv.index('--job') + 1]}\n")
            return 0
        case ["pr", "checks", _number, *_]:
            return _pr_checks(argv)
        case ["pr", "merge", number, *_] if "--squash" in argv:
            return _merge(number, auto="--auto" in argv)
    sys.stderr.write(f"fake gh: nothing here answers {argv}\n")
    return 1


# llmlint: ignore-end[e2e_not_mocked]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
