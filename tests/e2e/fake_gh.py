#!/usr/bin/env python3
"""The remote host's decisioning, as the program `onevcs` runs for `gh`.

`ONEVCS_GH` is the seam `onevcs` publishes for exactly this: it names the program
every `gh` call goes through, and `onevcs`'s own suite substitutes it the same way.
Nothing else about a publication is substituted here — the branch is pushed with real
git into a real bare origin — judged by a real `pre-push` hook where the journey asks
for one — and the change request this records is opened by the real `onevcs` from the
real recipe.

What it answers is the calls opening a change request makes: who is authenticated, which
changes already exist for a head and base, the `pr create` itself, the head commit of
what was just created, whether it is a draft, which checks its base requires, the `pr
ready` that lifts one, and each repository's visibility — public, so no repository's
name is a term another one's publication must keep out of what it writes. The base is
unprotected unless `FAKE_GH_STATE/checks.json` scripts a watch.
That file contains one `statusCheckRollup` list per poll, with the last repeated;
classic protection then requires `aggregate`. The draft journeys model a workflow
whose aggregate waits on other jobs and which has no `ready_for_review` trigger:
lifting changes only the draft flag and never starts another run. Workflow-runs
and check-suites reads return GitHub's empty-list shapes for that idle head.
Every call is appended to `FAKE_GH_CALLS` as JSON argv, and the body of each
opened change request is written to `FAKE_GH_STATE/pr-<n>.body` — byte for byte, because
"the change request carries exactly the drafted body" is a claim about characters and an
assertion against anything but the recorded file would be a claim about this program
instead.

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

It is one host holding several repositories, as GitHub is. Every call is answered for
the repository it names — `--repo`, or the `repos/<owner>/<name>/` an `api` path is
under — and `REPOSITORY` where it names none, so each repository numbers its change
requests from 1 as GitHub does, and two repositories' change requests collide on
number while staying two change requests. `REPOSITORY`'s records stay at the top of
`FAKE_GH_STATE`, where the journeys written against one repository read them; every
other repository's are under `repositories/<owner>/<name>/` there. `FAKE_GH_ORIGINS`
maps a repository to its bare origin, beside the one `FAKE_GH_ORIGIN` names for
`REPOSITORY`. What the host does on its own — a person merging or closing a change request,
or one opened by a build that is no longer installed — is `hold`, `merge` and `close`
below, which write exactly the record a call reads back.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal, NamedTuple, NotRequired, TypedDict, cast, get_args

#: Where each call's argv is appended, one JSON list per line.
CALLS_ENV = "FAKE_GH_CALLS"
#: The directory holding what this host remembers between calls.
STATE_ENV = "FAKE_GH_STATE"
#: The bare origin a created change request reads its head commit from, so the sha it
#: reports is the one real git actually put there.
ORIGIN_ENV = "FAKE_GH_ORIGIN"
#: Each other repository's bare origin, as a JSON object keyed by `owner/name`.
ORIGINS_ENV = "FAKE_GH_ORIGINS"
#: A repository as `gh` names one, `owner/name`: what keeps each one's change requests apart.
type Repository = str
#: A commit's full sha, as the origin reports it.
type Commit = str

#: The repository a call naming none is answered for.
REPOSITORY: Repository = "acme-corp/hosted"
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


def records(state: Path, repository: Repository) -> Path:
    """Where this host keeps one repository's change requests, under `state`."""
    held = state if repository == REPOSITORY else state / "repositories" / repository
    held.mkdir(parents=True, exist_ok=True)
    return held


def _repository(argv: list[str]) -> Repository:
    """The repository a call is about: the one it names, or `REPOSITORY`."""
    for flag in ("--repo", "-R"):
        if flag in argv[:-1]:
            return argv[argv.index(flag) + 1]
    if argv[:1] == ["api"] and len(argv) > 1 and argv[1].startswith("repos/"):
        owner, name = argv[1].split("/")[1:3]
        return f"{owner}/{name}"
    return REPOSITORY


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
    #: `--repo`, or the repository an `api` path is under: `REPOSITORY` where neither.
    repository: Repository = REPOSITORY
    #: `--state`, which change requests `pr list` answers with: gh's own default, `open`.
    state: str = "open"


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
    #: `gh`'s own shape for the commit a merge reached the base as: `{"oid": sha}` once
    #: merged, and `None` while it is not. What the installed `onevcs` reads it as is held by
    #: the sweep journey's merged branch, which retires only on this field read as onevcs
    #: reads it, so a shape drifting from the reader fails there by name.
    mergeCommit: MergeCommit | None
    #: Whether `pr merge --auto` armed it, for the host to merge once it may.
    armed: bool
    mergedAt: str | None
    #: The issues the host says it closes, each `owner/name#N`: written only where a journey
    #: has a person merge it closing some (:func:`merged_by_person`), and none otherwise.
    closes: NotRequired[list[str]]


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
        repository=_repository(argv),
        state=read.get("state", "open"),
    )


def _remembered(repository: Repository) -> list[Change]:
    """Every change request this host holds for one repository, in number order."""
    # `cast` rather than a check: every one of these files was written by `_create` or
    # `hold` below, so the shape is this program's own and a JSON round trip is the only
    # reason it is not statically known. A validator here would be asserting against
    # itself.
    return [
        cast(Change, json.loads(record.read_text(encoding="utf-8")))
        for record in sorted(records(_state(), repository).glob("pr-*.json"))
    ]


def _origin(repository: Repository) -> str:
    """The bare origin one repository's branches are really pushed to, if it has one."""
    if repository == REPOSITORY:
        return os.environ.get(ORIGIN_ENV, "")
    origins: dict[str, str] = json.loads(os.environ.get(ORIGINS_ENV) or "{}")
    return origins.get(repository, "")


def _head_sha(repository: Repository, head: str) -> Commit:
    """What the origin really has on that branch, read from the origin itself."""
    origin = _origin(repository)
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
    held = records(_state(), call.repository)
    number = 1
    while (held / f"pr-{number}.json").exists():
        number += 1
    (held / f"pr-{number}.body").write_text(call.body, encoding="utf-8")
    opened = hold(
        _state(),
        call.repository,
        number=number,
        head=call.head,
        head_sha=_head_sha(call.repository, call.head),
        base=call.base,
        title=call.title,
        draft=draft,
    )
    sys.stdout.write(f"{opened['url']}\n")
    return 0


def hold(
    state: Path,
    repository: Repository,
    *,
    number: int,
    head: str,
    head_sha: Commit,
    base: str = "main",
    title: str = "",
    draft: bool = False,
) -> Change:
    """The host holding change request `number` of `repository` open from `head`.

    What `pr create` records, and what a change request opened by a build that is no
    longer installed left on the host — the second being what a journey seeds.
    """
    opened: Change = {
        "number": number,
        "url": f"https://github.com/{repository}/pull/{number}",
        "state": "OPEN",
        "headRefOid": head_sha,
        "isDraft": draft,
        "head": head,
        "base": base,
        "title": title,
        "armed": False,
        "mergeCommit": None,
        "mergedAt": None,
    }
    path = records(state, repository) / f"pr-{number}.json"
    path.write_text(json.dumps(opened), encoding="utf-8")
    return opened


def merge(state: Path, repository: Repository, *, number: int, commit: Commit) -> Change:
    """A person merging change request `number` of `repository` on the host.

    The host reports it `MERGED`, at `commit` on the base, from the head it held — which
    is what a later `pr view` answers, and nothing else about it moves.
    """
    path = records(state, repository) / f"pr-{number}.json"
    # `cast` for the same reason as in `_remembered`: `hold` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    record["state"] = "MERGED"
    record["isDraft"] = False
    record["mergeCommit"] = {"oid": commit}
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


def merged_by_person(
    state: Path, repository: Repository, origin: Path, *, number: int, closes: list[str]
) -> Change:
    """A person squash-merging change request `number` of `repository`, closing `closes`.

    The squash is made in the bare origin as GitHub makes one — the head's tree on the base —
    so the commit the host then reports merged is one real git put there, and the issues
    are the ones GitHub would answer as the change's `closingIssuesReferences`.
    """
    path = records(state, repository) / f"pr-{number}.json"
    # `cast` for the same reason as in `_remembered`: `hold` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    record["closes"] = closes
    path.write_text(json.dumps(record), encoding="utf-8")
    return _merge_now(path, record, repository, origin=str(origin))


def _closing_references(record: Change) -> list[dict[str, object]]:
    """The issues a change closes, in `gh`'s shape for `closingIssuesReferences`."""
    references: list[dict[str, object]] = []
    for reference in record.get("closes", []):
        slug, number = reference.split("#")
        owner, name = slug.split("/")
        references.append(
            {"number": int(number), "repository": {"name": name, "owner": {"login": owner}}}
        )
    return references


def close(state: Path, repository: Repository, *, number: int) -> Change:
    """A person closing change request `number` of `repository` without merging it."""
    path = records(state, repository) / f"pr-{number}.json"
    # `cast` for the same reason as in `_remembered`: `hold` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    record["state"] = "CLOSED"
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


def _view(number: str, call: Call) -> int:
    """Exactly the fields the call asked for, of the repository it named, as `gh` answers."""
    path = records(_state(), call.repository) / f"pr-{number}.json"
    if not path.exists():
        sys.stderr.write(
            f"GraphQL: Could not resolve to a PullRequest with the number of {number}. "
            "(repository.pullRequest)\n"
        )
        return 1
    # `cast` for the same reason as in `_remembered`: `hold` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    if record["armed"] and record["state"] == "OPEN" and (_state() / MERGE_PERMIT).exists():
        record = _merge_now(path, record, call.repository)
    # Projected off a plain mapping of the record, because which fields `--json` names is
    # the caller's to choose and a fixed field cannot stand in for a chosen one.
    remembered: dict[str, object] = {
        **record,
        "statusCheckRollup": [check.rollup(lifted=not record["isDraft"]) for check in _checks()],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "closingIssuesReferences": _closing_references(record),
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


def _ready(number: str, call: Call) -> int:
    """Lift a draft, as `gh pr ready` does: the record says so from then on."""
    path = records(_state(), call.repository) / f"pr-{number}.json"
    # `cast` for the same reason as in `_remembered`: `_create` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    record["isDraft"] = False
    if (_state() / "checks.json").exists():
        (_state() / "ready-poll").write_text(
            (_state() / "poll").read_text(encoding="utf-8"), encoding="utf-8"
        )
    path.write_text(json.dumps(record), encoding="utf-8")
    return 0


def _merge(number: str, call: Call, *, auto: bool) -> int:
    """`pr merge --squash [--auto]`: merge now when the host may, otherwise arm it."""
    path = records(_state(), call.repository) / f"pr-{number}.json"
    # `cast` for the same reason as in `_remembered`: `_create` wrote this file.
    record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
    if record["state"] != "OPEN":
        return 0
    if auto and not (_state() / MERGE_PERMIT).exists():
        record["armed"] = True
        path.write_text(json.dumps(record), encoding="utf-8")
        return 0
    _merge_now(path, record, call.repository)
    return 0


def _merge_now(
    path: Path, record: Change, repository: Repository, *, origin: str | None = None
) -> Change:
    """Squash the head's tree onto the base in the bare origin, and record the merge."""
    origin = origin or _origin(repository)

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


def _pr_checks(argv: list[str], call: Call) -> int:
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
        {
            "name": check.name,
            "link": f"https://github.com/{call.repository}/actions/runs/1/job/{index}",
        }
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


def _merge_scripted(number: str, call: Call) -> int:
    """`pr merge` under a `checks.json` watch: refused on a draft, else fast-forwarded."""
    record_path = records(_state(), call.repository) / f"pr-{number}.json"
    # `_create` wrote this Change; deserialization alone loses its type.
    record = cast(Change, json.loads(record_path.read_text(encoding="utf-8")))
    if record["isDraft"]:
        sys.stderr.write("cannot merge a draft\n")
        return 1
    subprocess.run(
        [
            "git",
            "--git-dir",
            _origin(call.repository),
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


#: Each change request a standings read names, as its alias and number.
STANDINGS_ALIAS = re.compile(r"\bc(\d+): pullRequest\(number: (\d+)\)")


def _field(argv: list[str], name: str) -> str:
    """The value of `-f <name>=<value>` in an `api graphql` call."""
    for at, word in enumerate(argv[:-1]):
        if word in ("-f", "-F") and argv[at + 1].startswith(f"{name}="):
            return argv[at + 1].split("=", 1)[1]
    return ""


def _empty(connection: str) -> dict[str, object]:
    return {connection: {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}


def _standings(argv: list[str]) -> int:
    """The batched read of a repository's change requests `onevcs` reconciles a run's against.

    Each alias `c<n>` answers its change request as the host holds it — merged by a person
    included, with the issues it closes — and the repository declares no ruleset and no
    classic protection, so nothing is required of any base. Review connections, when the
    read asks for them, are empty: no change request here was ever reviewed.
    """
    query = _field(argv, "query")
    repository = f"{_field(argv, 'owner')}/{_field(argv, 'name')}"
    answered: dict[str, object] = {}
    if "defaultBranchRef" in query:
        answered |= {"defaultBranchRef": {"name": "main"}}
        answered |= _empty("rulesets") | _empty("branchProtectionRules")
    for alias, number in STANDINGS_ALIAS.findall(query):
        path = records(_state(), repository) / f"pr-{number}.json"
        if not path.exists():
            answered[f"c{alias}"] = None
            continue
        # `cast` for the same reason as in `_remembered`: `hold` wrote this file.
        record = cast(Change, json.loads(path.read_text(encoding="utf-8")))
        pull: dict[str, object] = {
            "url": record["url"],
            "number": record["number"],
            "state": record["state"],
            "isDraft": record["isDraft"],
            "body": (path.parent / f"pr-{number}.body").read_text(encoding="utf-8")
            if (path.parent / f"pr-{number}.body").exists()
            else "",
            "baseRefName": record["base"],
            "headRefName": record["head"],
            "headRefOid": record["headRefOid"],
            "mergedAt": record["mergedAt"],
            "mergeCommit": record["mergeCommit"],
            "baseRef": {"name": record["base"]},
            "closingIssuesReferences": {"nodes": _closing_references(record)},
            "headRef": {"target": {"statusCheckRollup": {"contexts": {"nodes": []}}}},
        }
        if "reviewThreads" in query:
            pull |= _empty("reviewThreads") | _empty("comments") | _empty("reviews")
        answered[f"c{alias}"] = pull
    document = {"data": {"rateLimit": {"cost": 1}, "repository": answered}}
    sys.stdout.write(json.dumps(document) + "\n")
    return 0


def _list(call: Call) -> int:
    """Every change request in the asked state for that head and base, as `gh` filters them."""
    matched = [
        {
            "number": change["number"],
            "url": change["url"],
            "state": change["state"],
            "headRefOid": change["headRefOid"],
        }
        for change in _remembered(call.repository)
        if change["head"] == call.head
        and change["base"] == call.base
        and call.state in ("all", change["state"].lower())
    ]
    sys.stdout.write(json.dumps(matched) + "\n")
    return 0


def main(argv: list[str]) -> int:
    _record(argv)
    call = _call(argv)
    match argv:
        case ["api", "graphql", *_] if "pullRequest(number:" in _field(argv, "query"):
            return _standings(argv)
        case ["api", "user", *_]:
            sys.stdout.write("tester\n")
            return 0
        case ["api", path, *_] if path == f"repos/{call.repository}":
            # Every repository here is public, so no repository's name is a term another
            # one's publication must keep out of what it writes.
            sys.stdout.write(json.dumps({"visibility": "public", "private": False}) + "\n")
            return 0
        case ["api", path, *_] if path.startswith(f"repos/{call.repository}/rules/branches/"):
            # Unscripted, no ruleset applies to the base, so none of its rules requires a check.
            return _rules()
        case ["api", path, *_] if path.startswith(
            f"repos/{call.repository}/branches/"
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
            return _merge_scripted(number, call)
        case ["pr", "create", *_]:
            return _create(call, draft="--draft" in argv)
        case ["pr", "list", *_]:
            return _list(call)
        case ["pr", "view", number, *_]:
            return _view(number, call)
        case ["pr", "ready", number, *_]:
            return _ready(number, call)
        case ["run", "view", *_] if "--log" in argv:
            # The log `onevcs` keeps beside a check it records; its content is not this
            # host's to decide, only that the host produced one.
            sys.stdout.write(f"log of {argv[argv.index('--job') + 1]}\n")
            return 0
        case ["pr", "checks", _number, *_]:
            return _pr_checks(argv, call)
        case ["pr", "merge", number, *_] if "--squash" in argv:
            return _merge(number, call, auto="--auto" in argv)
    sys.stderr.write(f"fake gh: nothing here answers {argv}\n")
    return 1


# llmlint: ignore-end[e2e_not_mocked]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
