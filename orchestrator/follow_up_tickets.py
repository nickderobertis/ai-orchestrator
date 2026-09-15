"""The one source of a verified follow-up ticket's shape, and of who owns what on the board.

A run's **drafts** are unverified (`orchestrator/follow_up_drafts.py`). After the run, `just
follow-ups <run-id>` dispatches a follow-up agent that verifies each draft against the trees
it names, groups what stands by root cause, and writes one **ticket** per root cause into the
same `drafts` source — `tasks/<run-id>/tickets/<root-cause>.md` — before copying it onto the
`followups` board, where every session's tickets accumulate. Two stored shapes cross that
seam and both outlive the agent that wrote them, so both are decided here and nowhere else:

* **the ticket** (contract C5): its path, its front matter, its body headings, and the
  `onetaskgraph task copy` that is the only way it reaches the board;
* **ownership on the board** (contract C6): an issue belongs to the run its ticket's
  `created_by_run` names, a comment to the run its last-line marker names, and a follow-up
  run changes nothing that belongs to another run.

**Three readers, one shape.** `scripts/follow-ups.sh` counts a run's drafts and tickets,
composes the agent's task — rendering both contracts into it from :func:`ticket_contract`
and :func:`comment_contract` rather than restating them — and names every ticket that fails
the shape once an attached run settles. The agent validates each ticket it writes through
this module's `validate` command, and decides the status it carries through its
`board-status` command, before copying it. And the journeys read the board back through
:func:`from_store_item` and :func:`comment_owner`.

**The board is the record of the user's decision, and a ticket names its machine.** A ticket
reaches the board as a proposal and a person accepts it there, so the status a copy writes
is read off the board item first (:func:`status_before_copy`) rather than carried over from
the ticket. Evidence is a claim about trees read on one machine, so the record's `host` and
its `## Evidence` section both name the host the verification ran on.

**A ticket is read through the store, never parsed here.** An agent writes the front matter
in whatever YAML style it likes, and what reaches the board is what the installed
`onetaskgraph` reads out of that file — so the store's own reading is the one worth
validating. It is asked with the environment the launch exported, because the `drafts`
source's root is composed by `scripts/follow-up-env.sh` alone.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, NewType, NoReturn

from orchestrator import follow_up_drafts as drafts
from orchestrator import plan_store
from orchestrator.plan_store import RECORD_COMPONENT
from orchestrator.project_store import TASKS_DIRECTORY, frontmatter, hosted_origin

#: The plan-store source a ticket is stored in, and the directory beside a run's drafts it
#: is stored under — both the drafts module's, since a ticket sits in the run's draft tree.
SOURCE = drafts.SOURCE
TICKETS = drafts.TICKETS

#: The board a ticket is copied onto when a caller names none, as `onetaskgraph.yaml` names
#: it. `tests/test_plan_source_roots.py` holds the source's five values.
BOARD = "followups"

#: The version of the record below. A reader refuses any other: a ticket is a stored shape
#: that outlives the agent that wrote it. Schema 2 added `host` and the proposal statuses;
#: schema 3 added `repositories`, which files a ticket's issue in its root cause's repository.
SCHEMA = 3

#: The metadata key a ticket's record sits under, which travels onto the board item.
KEY = "orchestrator.follow-up"


class Status(StrEnum):
    """A ticket's status, as the plan store's category of its board item.

    **The board is the record of the user's decision.** A ticket reaches it as a proposal,
    and a person moving the item to `todo` is what accepts it, so a later agent selects
    accepted tickets by this category alone. The `followups` source maps `backlog` to the
    board's `Proposal` option (`onetaskgraph.yaml`); a withdrawn ticket's issue is closed as
    not planned, and no issue is ever deleted.
    """

    PROPOSED = "backlog"
    ACCEPTED = "todo"
    UNDER_WAY = "in-progress"
    FINISHED = "done"
    WITHDRAWN = "cancelled"

    @property
    def meaning(self) -> str:
        """What this status says about a ticket."""
        return _MEANINGS[self]

    @property
    def accepted(self) -> bool:
        """Whether a person accepted the ticket, which only a person undoes."""
        return self in (Status.ACCEPTED, Status.UNDER_WAY, Status.FINISHED)

    @property
    def written(self) -> str:
        """The word a ticket's `status` is written as, which the store reads as this category.

        The category's own spelling for every status but one: onetaskgraph 0.2.31's
        `local-md` source reads `in-progress` as `unknown` and `in progress` as
        `in-progress`. `tests/test_follow_up_tickets.py` reads every word back through the
        installed store.
        """
        return "in progress" if self is Status.UNDER_WAY else self.value


_MEANINGS = {
    Status.PROPOSED: "a proposal awaiting the user's decision",
    Status.ACCEPTED: "accepted",
    Status.UNDER_WAY: "accepted and under way",
    Status.FINISHED: "accepted and finished",
    Status.WITHDRAWN: "withdrawn",
}


#: The identities a ticket names, each a type of its own so that one cannot be passed where
#: another is meant: the run a ticket or comment belongs to, the root cause's slug, the
#: normalized origin a root cause lives in, a commit, a qualified draft id, and an RFC 3339
#: UTC time.
RunId = NewType("RunId", str)
RootCause = NewType("RootCause", str)
Origin = NewType("Origin", str)
Commit = NewType("Commit", str)
QualifiedDraftId = NewType("QualifiedDraftId", str)
Timestamp = NewType("Timestamp", str)
Host = NewType("Host", str)


class Basis(NamedTuple):
    """One repository a ticket's claims were verified in, and the commit they were verified at."""

    origin: Origin
    commit: Commit


#: How long a ticket's title may be.
TITLE_LIMIT = 120

#: The keys of a ticket's record, every one present, in the order they are written.
RECORD_KEYS = (
    "schema",
    "root_cause",
    "repository",
    "created_by_run",
    "owning_runs",
    "drafts",
    "basis",
    "verified_at",
    "host",
)

#: The level-2 headings a ticket's body carries, in this order, each with content.
HEADINGS = (
    "Root cause",
    "Repository",
    "Examples",
    "Evidence",
    "Suggested fixes",
    "Owning runs",
)

#: The heading whose section names the host the verification ran on.
EVIDENCE = "Evidence"

#: A root cause's slug, which names the ticket's file.
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

#: A commit a claim was verified at.
COMMIT = re.compile(r"[0-9a-f]{40}")

#: The host a ticket's verification ran on, as `hostname` prints it: at most this many
#: characters, of dot-separated labels, each 1–63 ASCII letters, digits and `-`, neither
#: starting nor ending with `-`. Its shape is checked, never its value.
HOST_LIMIT = 253
HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")

#: A qualified draft id a ticket consumed.
DRAFT_ID = re.compile(rf"{re.escape(SOURCE)}:(?P<run>[^/\s]+)/{re.escape(drafts.DRAFTS)}/[^/\s]+")

#: The last line of a comment a follow-up run owns, and the visible line it opens with.
COMMENT_MARKER = '<!-- orchestrator:follow-up-comment run="{run}" root_cause="{root_cause}" -->'
COMMENT_MARKER_LINE = re.compile(
    r"<!-- orchestrator:follow-up-comment"
    r' run="(?P<run>[^"\s]+)" root_cause="(?P<root_cause>[^"\s]+)" -->'
)
COMMENT_OPENING = "Additional evidence from follow-up run `{run}`."

#: The suffix a ticket's file carries.
TICKET_SUFFIX = ".md"

#: The placeholders the task template is filled at: values it may name as often as its
#: prose needs them, and sections it names exactly once, since a section rendered twice is
#: two copies of a contract in one task.
PLACEHOLDER = re.compile(r"@([A-Z][A-Z_]*)@")
VALUES = ("RUN", "BOARD", "DRAFTS_ROOT", "VALIDATE", "BOARD_STATUS", "CHECKOUT")
SECTIONS = ("TICKET_CONTRACT", "COMMENT_CONTRACT", "REDISPATCH", "FEEDBACK")
PLACEHOLDERS = (*VALUES, *SECTIONS)

#: This command's name in its diagnostics.
PROG = "follow-up-tickets"

#: Exit statuses: every ticket is sound; a ticket failed the shape; the command could not run.
#: And three of `board-status`'s own, each a ticket not to copy: the board holds its item at a
#: category no ticket carries; a withdrawal the board's acceptance of the item refuses; and a
#: ticket whose repository is not one of the board's owner, which nothing asks the board about.
SOUND = 0
UNSOUND = 1
UNRUNNABLE = 2
UNPLACED = 3
ACCEPTED = 4
OUTSIDE_OWNER = 5

#: The host every repository a ticket's issue may be filed in lives on.
GITHUB = "github.com"

#: What a dry-run `onetaskgraph task copy` reports for a ticket the board holds no item for,
#: and each action it reports for one whose item the board already holds.
CREATED = "created"
EXISTING = ("updated", "unchanged")


class Refused(ValueError):
    """A ticket, or an input to one, without the shape this module states; says every reason."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class Ticket:
    """One verified follow-up ticket, every field validated."""

    title: str
    status: Status
    root_cause: RootCause
    repository: Origin
    created_by_run: RunId
    owning_runs: tuple[RunId, ...]
    drafts: tuple[QualifiedDraftId, ...]
    basis: tuple[Basis, ...]
    verified_at: Timestamp
    host: Host
    body: str


class CommentOwner(NamedTuple):
    """What a follow-up run's comment marker names."""

    run: RunId
    root_cause: RootCause


def qualified_id(run: str, root_cause: str) -> str:
    """The ticket of ``run`` for ``root_cause``, as the store addresses it."""
    return f"{SOURCE}:{run}/{TICKETS}/{root_cause}"


def ticket_path(root: Path, run: str, root_cause: str) -> Path:
    """Where that ticket's file is under a `drafts` root."""
    return root / TASKS_DIRECTORY / run / TICKETS / f"{root_cause}{TICKET_SUFFIX}"


def record(ticket: Ticket) -> dict[str, object]:
    """The metadata a ticket is stored under, every key present."""
    return {
        "schema": SCHEMA,
        "root_cause": ticket.root_cause,
        "repository": ticket.repository,
        "created_by_run": ticket.created_by_run,
        "owning_runs": list(ticket.owning_runs),
        "drafts": list(ticket.drafts),
        "basis": {entry.origin: entry.commit for entry in ticket.basis},
        "verified_at": ticket.verified_at,
        "host": ticket.host,
    }


def render(ticket: Ticket) -> str:
    """One ticket as the `local-md` record it is stored as.

    No `project`, and `repositories` naming exactly the record's `repository` — derived from
    it rather than held beside it, so nothing written here can make the two differ.
    """
    return frontmatter(
        {
            "title": ticket.title,
            "status": ticket.status.written,
            "repositories": [ticket.repository],
            "metadata": {KEY: record(ticket)},
        },
        ticket.body,
    )


def repository_name(origin: str) -> str:
    """The last segment of a normalized origin, which a ticket's title opens with."""
    return origin.rsplit("/", 1)[-1]


def _is_run(value: object) -> bool:
    return isinstance(value, str) and RECORD_COMPONENT.fullmatch(value) is not None


def _is_origin(value: object) -> bool:
    return isinstance(value, str) and hosted_origin(value) == value


def _title_problems(title: object, repository: object) -> list[str]:
    if not isinstance(title, str) or not title.strip():
        return [
            "the title is empty; a ticket's title is "
            "`<repository name>: <the root cause in one line>`"
        ]
    found = []
    if title != title.strip() or drafts.CONTROL.search(title):
        found.append(
            "the title carries surrounding whitespace, a line break or a control character"
        )
    if len(title) > TITLE_LIMIT:
        found.append(
            f"the title is {len(title)} characters, over the {TITLE_LIMIT} a ticket's "
            "title may hold"
        )
    if isinstance(repository, str) and _is_origin(repository):
        prefix = f"{repository_name(repository)}: "
        if not title.startswith(prefix) or not title.removeprefix(prefix).strip():
            found.append(
                f"the title {title!r} does not read `{prefix}<the root cause in one line>`"
            )
    return found


def _runs_problems(runs: object, creator: object) -> list[str]:
    if not isinstance(runs, list) or not runs:
        return ["`owning_runs` is not a non-empty list of run ids"]
    found = [f"`owning_runs` entry {one!r} is not a run id" for one in runs if not _is_run(one)]
    if len({str(one) for one in runs}) != len(runs):
        found.append("`owning_runs` names a run twice")
    if isinstance(creator, str) and creator not in runs:
        found.append(f"`owning_runs` does not include `created_by_run` {creator!r}")
    return found


def _drafts_problems(consumed: object, runs: object) -> list[str]:
    if not isinstance(consumed, list) or not consumed:
        return ["`drafts` is not a non-empty list of qualified draft ids"]
    found = []
    for draft in consumed:
        matched = DRAFT_ID.fullmatch(draft) if isinstance(draft, str) else None
        if matched is None:
            found.append(
                f"`drafts` entry {draft!r} is not a qualified draft id like "
                f"{SOURCE}:<run-id>/{drafts.DRAFTS}/<draft-id>"
            )
        elif isinstance(runs, list) and matched["run"] not in runs:
            found.append(f"`drafts` entry {draft!r} belongs to a run `owning_runs` does not name")
    return found


def _basis_problems(basis: object, repository: object) -> list[str]:
    if not isinstance(basis, Mapping) or not basis:
        return ["`basis` is not a non-empty mapping of normalized origin to commit"]
    found = [
        f"`basis` entry {origin!r}: {commit!r} is not a normalized origin and a 40-character commit"
        for origin, commit in basis.items()
        if not _is_origin(origin) or not isinstance(commit, str) or not COMMIT.fullmatch(commit)
    ]
    if isinstance(repository, str) and repository not in basis:
        found.append(f"`basis` names no commit for the ticket's own repository {repository!r}")
    return found


def _real_time(value: object) -> bool:
    if not isinstance(value, str) or not drafts.DRAFTED_AT.fullmatch(value):
        return False
    try:
        datetime.strptime(value, drafts.DRAFTED_AT_FORMAT)  # noqa: DTZ007 - the spelling is UTC's own `Z`
    except ValueError:
        return False
    return True


def is_host(value: object) -> bool:
    """Whether ``value`` has a hostname's shape; which host it names is never checked."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= HOST_LIMIT
        and all(HOST_LABEL.fullmatch(label) for label in value.split("."))
    )


def _record_problems(
    held: Mapping[str, object], *, run: str | None, root_cause: str | None
) -> list[str]:
    found = []
    # Named before the missing keys, because a ticket of an older schema lacks the keys its
    # successor added, and the schema is what says why.
    if "schema" in held and (type(held["schema"]) is not int or held["schema"] != SCHEMA):
        found.append(
            f"the record is schema {held['schema']!r}, and this reads schema {SCHEMA}; bring "
            "the ticket to the current shape"
        )
    missing = [key for key in RECORD_KEYS if key not in held]
    if missing:
        return [*found, f"the `{KEY}` record is missing {', '.join(missing)}"]
    if unexpected := sorted(key for key in held if key not in RECORD_KEYS):
        found.append(
            f"the `{KEY}` record carries keys this does not write: {', '.join(unexpected)}"
        )
    stated = held["root_cause"]
    if not isinstance(stated, str) or not SLUG.fullmatch(stated):
        found.append(f"`root_cause` {stated!r} is not a kebab-case slug")
    elif root_cause is not None and stated != root_cause:
        found.append(f"`root_cause` {stated!r} is not the file's root cause {root_cause!r}")
    repository = held["repository"]
    if not _is_origin(repository):
        found.append(
            f"`repository` {repository!r} is not a normalized origin like github.com/owner/name"
        )
    creator = held["created_by_run"]
    if not _is_run(creator):
        found.append(f"`created_by_run` {creator!r} is not a run id")
    elif run is not None and creator != run:
        found.append(
            f"`created_by_run` {creator!r} is not {run!r}, the run whose tickets directory holds it"
        )
    found.extend(_runs_problems(held["owning_runs"], creator))
    found.extend(_drafts_problems(held["drafts"], held["owning_runs"]))
    found.extend(_basis_problems(held["basis"], repository))
    if not _real_time(held["verified_at"]):
        found.append(
            f"`verified_at` {held['verified_at']!r} is not an RFC 3339 UTC time like "
            "YYYY-MM-DDTHH:MM:SSZ"
        )
    if not is_host(held["host"]):
        found.append(
            f"`host` {held['host']!r} is not a hostname: at most {HOST_LIMIT} characters of "
            "dot-separated labels, each 1–63 ASCII letters, digits and `-`, neither starting "
            "nor ending with `-`; write what `hostname` prints"
        )
    return found


def _repositories_problems(repositories: object, repository: object) -> list[str]:
    """How `repositories` is not exactly the record's `repository`, where the issue is created."""
    match repositories:
        case [named]:
            if _is_origin(repository) and named != repository:
                return [
                    f"the ticket's `repositories` names {named!r}, not its record's "
                    f"`repository` {repository!r}; the two name the one repository its issue "
                    "is created in"
                ]
            return []
        case [_, _, *_] as several:
            return [
                f"the ticket's `repositories` names {len(several)} entries; a ticket names "
                "exactly one, its record's `repository`, which is the repository its issue is "
                "created in"
            ]
        case _:
            return [
                "the ticket carries no `repositories`; a ticket names exactly one, its record's "
                "`repository`, which is the repository its issue is created in"
            ]


def _body_problems(body: object, host: object) -> list[str]:
    if not isinstance(body, str):
        return ["the ticket has no body"]
    found = drafts.sections(body)
    names = [name for name, _ in found]
    after = 0
    for required in HEADINGS:
        try:
            at = names.index(required, after)
        except ValueError:
            return [
                f"the body carries no `## {required}` heading in its place; a ticket's "
                "body carries "
                + ", ".join(f"`## {heading}`" for heading in HEADINGS)
                + ", in that order, each with content"
            ]
        if not found[at][1].strip():
            return [f"the body's `## {required}` section is empty"]
        if required == EVIDENCE and is_host(host) and str(host) not in found[at][1]:
            return [
                f"the body's `## {EVIDENCE}` section does not name the host {host!r} the "
                "record's `host` states; state there the host the verification ran on"
            ]
        after = at + 1
    return []


def problems(
    item: Mapping[str, object], *, run: str | None = None, root_cause: str | None = None
) -> list[str]:
    """Every way a store item, as `onetaskgraph task show --json` reports it, is not a ticket.

    ``run`` and ``root_cause`` are what the ticket's path says, when it was read from one.
    The record's problems come first, so a ticket of an older schema is named for its schema
    before anything its successor added.
    """
    found = []
    metadata = item.get("metadata")
    held = metadata.get(KEY) if isinstance(metadata, Mapping) else None
    if isinstance(held, Mapping):
        found.extend(_record_problems(held, run=run, root_cause=root_cause))
        repository, host = held.get("repository"), held.get("host")
    else:
        found.append(f"the ticket carries no `{KEY}` metadata record")
        repository = host = None
    if item.get("project") is not None:
        found.append(
            "the ticket carries a `project`; a ticket carries none, so it lands on the "
            "board as a standalone item"
        )
    found.extend(_repositories_problems(item.get("repositories"), repository))
    category = _category(item.get("status"))
    if category not in tuple(Status):
        found.append(
            f"the status is {category!r}, where a ticket's status is "
            + "; ".join(f"`{status}`, {status.meaning}" for status in Status)
            + " — write the one `board-status` prints"
        )
    found.extend(_title_problems(item.get("title"), repository))
    found.extend(_body_problems(item.get("content"), host))
    return found


def _category(status: object) -> object:
    """The category a store item's status reports, which is what a ticket's status is."""
    return status.get("category") if isinstance(status, Mapping) else status


def from_store_item(
    item: Mapping[str, object], *, run: str | None = None, root_cause: str | None = None
) -> Ticket:
    """The ticket a store item holds, or :class:`Refused` naming every problem."""
    found = problems(item, run=run, root_cause=root_cause)
    if found:
        raise Refused(found)
    metadata = item["metadata"]
    # `problems` has just proven every shape narrowed here, and says so if it has not.
    assert isinstance(metadata, Mapping)  # noqa: S101
    held = metadata[KEY]
    assert isinstance(held, Mapping)  # noqa: S101
    basis = held["basis"]
    assert isinstance(basis, Mapping)  # noqa: S101
    return Ticket(
        title=str(item["title"]),
        status=Status(str(_category(item["status"]))),
        root_cause=RootCause(str(held["root_cause"])),
        repository=Origin(str(held["repository"])),
        created_by_run=RunId(str(held["created_by_run"])),
        owning_runs=tuple(RunId(str(one)) for one in held["owning_runs"]),
        drafts=tuple(QualifiedDraftId(str(one)) for one in held["drafts"]),
        basis=tuple(
            sorted(
                Basis(Origin(str(origin)), Commit(str(commit))) for origin, commit in basis.items()
            )
        ),
        verified_at=Timestamp(str(held["verified_at"])),
        host=Host(str(held["host"])),
        body=str(item["content"]).strip(),
    )


def located_path(path: Path) -> tuple[str, str]:
    """The run and root cause a ticket file's path names, or :class:`Refused`."""
    run, root_cause = path.parent.parent.name, path.stem
    if (
        path.suffix != TICKET_SUFFIX
        or path.parent.name != TICKETS
        or path.parent.parent.parent.name != TASKS_DIRECTORY
        or not RECORD_COMPONENT.fullmatch(run)
        or not SLUG.fullmatch(root_cause)
    ):
        raise Refused(
            [
                f"{path} is not where a ticket is stored; a ticket is `<drafts root>/"
                f"{TASKS_DIRECTORY}/<run-id>/{TICKETS}/<root-cause>{TICKET_SUFFIX}`, with a "
                "kebab-case root cause"
            ]
        )
    return run, root_cause


def read_ticket(path: Path) -> Ticket:
    """The ticket at ``path``, read through the installed store; :class:`Refused` otherwise."""
    resolved = path.absolute()
    run, root_cause = located_path(resolved)
    ticket = qualified_id(run, root_cause)
    try:
        item = plan_store.one_item(plan_store.store_json(["task", "show", ticket]), "task")
    except OSError as exc:
        raise Refused(
            [
                f"the store could not read {ticket} ({exc}); validate it from the "
                "environment the follow-ups launch exported, which names the drafts root"
            ]
        ) from None
    location = item.get("location")
    stored = location.get("path") if isinstance(location, Mapping) else None
    if not isinstance(stored, str) or Path(stored).resolve() != resolved.resolve():
        raise Refused(
            [
                f"the store reads {ticket} from {stored!r}, not from {resolved}; validate "
                "a ticket under the drafts root the follow-ups launch exported"
            ]
        )
    return from_store_item(item, run=run, root_cause=root_cause)


class Unplaced(ValueError):
    """The board holds a ticket's item at a category no ticket carries, so it is not copied."""

    def __init__(self, category: str) -> None:
        super().__init__(
            f"the board holds this ticket's item at category {category!r}, which no ticket "
            "carries; copy nothing and report it"
        )


class AcceptedOnBoard(ValueError):
    """A withdrawal of a ticket the board shows as accepted, which only a person undoes."""

    def __init__(self, status: Status) -> None:
        super().__init__(
            f"the board holds this ticket's item at `{status}` ({status.meaning}), so this "
            "run never withdraws it: copy nothing, leave the local ticket as it is, and "
            "report that you would have withdrawn it and why"
        )
        self.status = status


class OutsideOwner(ValueError):
    """A ticket whose repository is not one of the board's owner, so its issue is filed nowhere.

    Filing an issue in a third party's public repository is an outward action nobody asked
    for, and the store compares no owner for an item with no parent, which a ticket is.
    """

    def __init__(self, repository: str, owner: str) -> None:
        super().__init__(
            f"the ticket's repository {repository!r} is not a repository of the board's owner "
            f"{owner!r} ({GITHUB}/{owner}/<name>), so its issue is filed nowhere: copy "
            "nothing, never change `repositories` to get it filed, and report it"
        )


def board_owner(board: str) -> str | None:
    """The owner ``board`` is configured with, or `None` for a source that configures none.

    Read through the store's own configuration, the way a source's root is. A source with no
    owner — a `local-md` store — files no issue in any repository, so it bounds nothing.
    """
    owner = plan_store.configured_settings().get(f"sources.{board}.config.owner")
    if owner is not None and (not isinstance(owner, str) or not owner):
        raise OSError(f"source {board!r} configures an owner {owner!r} that names no account")
    return owner


def ticket_repository(ticket: str) -> str:
    """The normalized origin the stored ticket's record names, read through the store."""
    item = plan_store.one_item(plan_store.store_json(["task", "show", ticket]), "task")
    metadata = item.get("metadata")
    held = metadata.get(KEY) if isinstance(metadata, Mapping) else None
    repository = held.get("repository") if isinstance(held, Mapping) else None
    if not isinstance(repository, str) or not _is_origin(repository):
        raise Refused(
            [
                f"{ticket} names no `repository` in its `{KEY}` record that is a normalized "
                "origin; validate the ticket before asking the board about it"
            ]
        )
    return repository


def under_owner(repository: str, owner: str) -> bool:
    """Whether a normalized origin is `github.com/<owner>/<name>`."""
    host, named_owner, _name = repository.split("/")
    return host == GITHUB and named_owner == owner


def board_category(ticket: str, board: str) -> str | None:
    """The category ``board`` holds ``ticket``'s item at, or `None` when it holds no item.

    Asked of the store's own copy, dry-run, because the origin a copy records is the only
    correspondence between a ticket and its item: the dry-run reports whether the copy would
    create an item or reach an existing one, and names that one.
    """
    planned = plan_store.store_json(["task", "copy", ticket, "--to", board, "--dry-run"])
    entries = planned.get("items")
    entry = entries[0] if isinstance(entries, list) and len(entries) == 1 else None
    action = entry.get("action") if isinstance(entry, Mapping) else None
    destination = entry.get("destination") if isinstance(entry, Mapping) else None
    if action == CREATED:
        return None
    if action not in EXISTING or not isinstance(destination, str):
        raise OSError(
            f"the dry-run copy of {ticket} onto {board} answered {entries!r}, naming neither "
            "a new item nor an existing one"
        )
    item = plan_store.one_item(plan_store.store_json(["task", "show", destination]), "task")
    return str(_category(item.get("status")))


def status_before_copy(held: str | None, *, withdraw: bool) -> Status:
    """The status a ticket is copied with, given the category the board holds its item at.

    A ticket the board holds no item for is a proposal; one it holds carries the board's
    status, so a copy never undoes a person's decision; and a withdrawal closes an item only
    while nobody has accepted it. :class:`Unplaced` or :class:`AcceptedOnBoard` otherwise.
    """
    if held is None:
        return Status.WITHDRAWN if withdraw else Status.PROPOSED
    if held not in tuple(Status):
        raise Unplaced(held)
    status = Status(held)
    if withdraw and status.accepted:
        raise AcceptedOnBoard(status)
    return Status.WITHDRAWN if withdraw else status


def comment_marker(run: str, root_cause: str) -> str:
    """The exact last line of a comment ``run`` owns about ``root_cause``."""
    return COMMENT_MARKER.format(run=run, root_cause=root_cause)


def comment_opening(run: str) -> str:
    """The visible first line of a comment ``run`` owns."""
    return COMMENT_OPENING.format(run=run)


def render_comment(run: str, root_cause: str, evidence: str) -> str:
    """A whole comment ``run`` adds to another run's issue: opening, evidence, marker."""
    return f"{comment_opening(run)}\n\n{evidence.strip()}\n\n{comment_marker(run, root_cause)}\n"


def comment_owner(body: str) -> CommentOwner | None:
    """The run and root cause a comment's last line names, or `None` when no run owns it.

    Both halves are held to their grammars, because the marker is stored text anybody with
    the board's credential can write: a value that is not a run id or a root-cause slug
    names no run's comment.
    """
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    matched = COMMENT_MARKER_LINE.fullmatch(lines[-1]) if lines else None
    if (
        matched is None
        or not RECORD_COMPONENT.fullmatch(matched["run"])
        or not SLUG.fullmatch(matched["root_cause"])
    ):
        return None
    return CommentOwner(RunId(matched["run"]), RootCause(matched["root_cause"]))


def issue_owner(item: Mapping[str, object]) -> RunId | None:
    """The run a board item's `created_by_run` names, or `None` when no run owns it."""
    metadata = item.get("metadata")
    held = metadata.get(KEY) if isinstance(metadata, Mapping) else None
    creator = held.get("created_by_run") if isinstance(held, Mapping) else None
    return RunId(creator) if isinstance(creator, str) and _is_run(creator) else None


def may_change_issue(run: str, item: Mapping[str, object]) -> bool:
    """Whether ``run`` may edit or close ``item``: only an issue it created."""
    return issue_owner(item) == run


def may_change_comment(run: str, body: str) -> bool:
    """Whether ``run`` may edit or delete a comment: only one its marker names."""
    owner = comment_owner(body)
    return owner is not None and owner.run == run


def may_comment_on(run: str, item: Mapping[str, object]) -> bool:
    """Whether ``run`` may comment on ``item``: another run's issue, never its own."""
    owner = issue_owner(item)
    return owner is not None and owner != run


#: What each body heading of the example ticket :func:`ticket_contract` renders says to write.
_HEADING_GUIDANCE = (
    "<a simple explanation of the root cause>",
    "<the normalized origin, and the paths inside it>",
    "<one or more examples of it>",
    "<the host the verification ran on, exactly as `hostname` printed it; then per draft: "
    "the qualified draft id, its run and node, the verified claim with `path:line` at the "
    "basis commit, and the transcript command from the draft>",
    "<the suggested fixes>",
    "<every run whose evidence this ticket carries>",
)


def ticket_contract(run: str, board: str) -> str:
    """C5, as the follow-up agent is told it: the shape of the ticket it writes."""
    example = Ticket(
        title="<repository name>: <the root cause in one line>",
        status=Status.PROPOSED,
        root_cause=RootCause("<root-cause>"),
        repository=Origin(
            "<normalized origin the root cause lives in, like github.com/owner/name>"
        ),
        created_by_run=RunId(run),
        owning_runs=(
            RunId(run),
            RunId("<every other run whose evidence this ticket's body carries>"),
        ),
        drafts=(QualifiedDraftId(f"{SOURCE}:{run}/{drafts.DRAFTS}/<draft-id>"),),
        basis=(
            Basis(
                Origin("<normalized origin>"),
                Commit("<the 40-character commit the claims were verified at>"),
            ),
        ),
        verified_at=Timestamp("<now, in RFC 3339 UTC: YYYY-MM-DDTHH:MM:SSZ>"),
        host=Host("<exactly what `hostname` prints on the machine you run on>"),
        body="\n\n".join(
            f"## {heading}\n\n{guidance}"
            for heading, guidance in zip(HEADINGS, _HEADING_GUIDANCE, strict=True)
        ),
    )
    ticket = qualified_id(run, "<root-cause>")
    headings = ", ".join(f"`## {heading}`" for heading in HEADINGS)
    return (
        f"A ticket is a local Markdown task in the `{SOURCE}` source, written to "
        f"`@DRAFTS_ROOT@/{TASKS_DIRECTORY}/{run}/{TICKETS}/<root-cause>{TICKET_SUFFIX}` — "
        f"qualified id `{ticket}` — where `<root-cause>` is a kebab-case slug.\n\n"
        "- It carries **no `project`**, so it lands on the board as a standalone item.\n"
        "- **Its `repositories` names exactly one normalized origin, its record's "
        "`repository`**: the repository the root cause lives in. Its issue is created in "
        "that one repository and added to the board as an item, and that repository must "
        "belong to the board's owner, as `github.com/<owner>/<name>`.\n"
        "- Its title is `<repository name>: <the root cause in one line>`, at most "
        f"{TITLE_LIMIT} characters, where the repository name is the last segment of "
        "`repository`.\n"
        "- **Its status is the board's to decide.** A new ticket is "
        f"`{Status.PROPOSED.written}`, {Status.PROPOSED.meaning}, which the board shows as "
        "`Proposal`; a person moving it to `Todo` is what accepts it. A ticket the board "
        "already holds carries the status the board holds it at, so a copy never undoes that "
        f"decision. A ticket this run withdraws is `{Status.WITHDRAWN.written}`, which the "
        "board holds as closed as not planned, unless the board shows it as accepted: then "
        "copy nothing, leave the local ticket as it is, and report that you would have "
        "withdrawn it and why. No issue is ever deleted.\n"
        f"- **Before every copy, run `@BOARD_STATUS@ --board {board} <path of the ticket>`** "
        "(adding `--withdraw` for a ticket this run withdraws, before you change that ticket), "
        "write the word it prints as the ticket's `status`, and validate the ticket again. It "
        f"exits {SOUND} with that word; {UNPLACED} when the board holds the item at a status "
        f"no ticket carries; {ACCEPTED} for a withdrawal of an item the board shows as "
        f"accepted; and {OUTSIDE_OWNER} when the ticket's repository is not one of the board's "
        "owner, before anything is asked of the board. On any of these refusals, copy nothing "
        "and report what it printed.\n"
        "- **A refusal is reported, never worked around.** When `board-status` exits "
        f"{OUTSIDE_OWNER}, or `onetaskgraph task copy` refuses the ticket — for a repository the "
        "token cannot see, or one GitHub will not create an issue in — copy nothing for that "
        "ticket, never retry it with `repositories` removed or changed to get it filed, and "
        "report what was printed.\n"
        f"- Its front matter carries the `{KEY}` record with every key present, and its body "
        f"the headings {headings}, in that order, each with content. `created_by_run` is "
        "this run, and `owning_runs` includes it.\n"
        "- **`host` is read, never typed.** Run `hostname` on the machine you run on and "
        f"write exactly what it prints, both as `host` and in the `## {EVIDENCE}` section, "
        "never a value you type or recall.\n"
        f"- It reaches the board only as `onetaskgraph task copy {ticket} --to {board}`. The "
        "origin that copy records makes a later copy of the same ticket update that same "
        "issue, and that is the only way an issue is edited.\n\n"
        "The shape, with every placeholder to fill:\n\n"
        f"````markdown\n{render(example)}````\n"
    )


def comment_contract(run: str, board: str) -> str:
    """C6, as the follow-up agent is told it: who owns what on the board."""
    return (
        f"- **Ownership is by run.** An issue on `{board}` belongs to the run its `{KEY}` "
        "record's `created_by_run` names. A comment belongs to the run named in its **last "
        "line**, which is exactly\n\n"
        f"  `{comment_marker(run, '<root-cause>')}`\n\n"
        f"  and its first line is visible to a reader: `{comment_opening(run)}`\n"
        f"- This run, `{run}`, may create, edit (by copying its ticket again) or close as not "
        f"planned only issues whose `created_by_run` is `{run}`. It may add, edit or delete "
        f"only comments whose marker names `{run}`. It never comments on an issue it "
        "created — it edits that issue instead — and it never changes an issue or a comment "
        "belonging to another run.\n"
        "- An open issue for the same root cause created by another run receives at most "
        "**one** comment from this run, carrying this run's evidence. Where this run's "
        "comment is already there, edit it with `onetaskgraph task comment edit`; never add "
        "a second.\n"
    )


#: The section a re-dispatch adds: when the manager sends feedback, or the run already
#: holds tickets from a follow-up agent before this one.
REDISPATCH = """\
## This is a re-dispatch

A follow-up agent has already worked run `@RUN@`'s drafts, so tickets, board issues and
comments of this run may already exist. The ownership rules above bind every change:

- change only what belongs to run `@RUN@` — an issue whose `created_by_run` is `@RUN@`, a
  comment whose marker names `@RUN@` — and nothing belonging to any other run;
- an issue run `@RUN@` created is **edited** — change its ticket and copy the ticket again —
  and never commented on;
- a comment run `@RUN@` already left on another run's issue is **edited** in place, never
  joined by a second;
- an existing ticket of run `@RUN@` is copied again carrying the board's status, which
  `@BOARD_STATUS@ --board @BOARD@ <path of the ticket>` prints, never the status the ticket
  held before — the board may have been moved since the last copy;
- a ticket of an older schema is brought to the current shape before it is copied, its
  `repositories` naming its record's `repository` and its `host` read from this machine
  with `hostname`, and validated again.
"""

#: The heading the manager's feedback goes under, above the feedback itself.
FEEDBACK = """\
## Feedback on the previous follow-up run

The manager's feedback on what the previous follow-up agent of run `@RUN@` produced,
verbatim. Act on it within the rules above.

"""


def _filled(text: str, values: Mapping[str, str]) -> str:
    """``text`` with each placeholder ``values`` names filled, and every other left as is."""
    return PLACEHOLDER.sub(lambda matched: values.get(matched[1], matched[0]), text)


def compose(
    template: str,
    *,
    run: str,
    board: str,
    drafts_root: Path,
    validate: str,
    board_status: str,
    checkout: Path,
    feedback: str | None,
    redispatch: bool,
) -> str:
    """The follow-up agent's task: ``template`` with every placeholder filled, once.

    The template is filled in a single pass, so what fills a placeholder is never read
    again for one — which is what brings the manager's feedback into the task verbatim,
    whatever it quotes. The contracts and sections carry the run and the drafts root
    themselves, so those are filled into them first.
    """
    named = PLACEHOLDER.findall(template)
    found = []
    if unknown := sorted(set(named) - set(PLACEHOLDERS)):
        found.append(f"the task template names placeholders nothing fills: {', '.join(unknown)}")
    if missing := [name for name in PLACEHOLDERS if name not in named]:
        found.append(f"the task template is missing placeholders: {', '.join(missing)}")
    if repeated := sorted(name for name in SECTIONS if named.count(name) > 1):
        found.append(f"the task template names these more than once: {', '.join(repeated)}")
    if found:
        raise Refused(found)
    scalars = {
        "RUN": run,
        "BOARD": board,
        "DRAFTS_ROOT": str(drafts_root),
        "VALIDATE": validate,
        "BOARD_STATUS": board_status,
        "CHECKOUT": str(checkout),
    }
    values = {
        **scalars,
        "TICKET_CONTRACT": _filled(ticket_contract(run, board), scalars),
        "COMMENT_CONTRACT": _filled(comment_contract(run, board), scalars),
        "REDISPATCH": _filled(REDISPATCH, scalars) if redispatch else "",
        "FEEDBACK": (
            "" if feedback is None else _filled(FEEDBACK, scalars) + feedback.rstrip() + "\n"
        ),
    }
    return PLACEHOLDER.sub(lambda matched: values[matched[1]], template)


def inventory(root: Path, run: str) -> tuple[int, int]:
    """How many drafts and tickets ``run`` holds under a `drafts` root."""
    base = root / TASKS_DIRECTORY / run
    return (
        len(list((base / drafts.DRAFTS).glob(f"*{TICKET_SUFFIX}"))),
        len(list((base / TICKETS).glob(f"*{TICKET_SUFFIX}"))),
    )


class _Parser(argparse.ArgumentParser):
    """A parser whose refusals exit with :data:`UNRUNNABLE` and say what to do next."""

    def error(self, message: str) -> NoReturn:
        self.exit(UNRUNNABLE, f"{PROG}: refused: {message}; run it with --help for the contract\n")


def _parser() -> _Parser:
    parser = _Parser(
        prog=PROG,
        description="Validate follow-up tickets, and compose the follow-up agent's task.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    root_help = "the drafts root the launch exported"
    validate = commands.add_parser("validate", help="validate ticket files through the store")
    validate.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    check = commands.add_parser("check-run", help="validate every ticket a run holds")
    check.add_argument("--root", type=Path, required=True, help=root_help)
    check.add_argument("run", metavar="RUN-ID")
    placed = commands.add_parser(
        "board-status",
        help="print the status a ticket is copied with, decided from the board's item",
    )
    placed.add_argument("--board", required=True, metavar="SOURCE")
    placed.add_argument(
        "--withdraw", action="store_true", help="decide for a ticket this run withdraws"
    )
    placed.add_argument("path", type=Path, metavar="PATH")
    count = commands.add_parser("inventory", help="print how many drafts and tickets a run holds")
    count.add_argument("--root", type=Path, required=True, help=root_help)
    count.add_argument("run", metavar="RUN-ID")
    task = commands.add_parser("compose", help="print the follow-up agent's task")
    task.add_argument("--template", type=Path, required=True)
    task.add_argument("--root", type=Path, required=True, help=root_help)
    task.add_argument("--run", required=True, metavar="RUN-ID")
    task.add_argument("--board", required=True, metavar="SOURCE")
    task.add_argument("--validate", required=True, metavar="COMMAND")
    task.add_argument("--board-status", required=True, metavar="COMMAND")
    task.add_argument("--checkout", type=Path, required=True, help="the launching checkout")
    task.add_argument("--feedback", type=Path, metavar="FILE")
    return parser


def _validated(paths: Sequence[Path]) -> int:
    status = SOUND
    for path in paths:
        try:
            read_ticket(path)
        except Refused as refusal:
            status = UNSOUND
            print(f"{PROG}: {path} is not a sound ticket:", file=sys.stderr)
            for problem in refusal.problems:
                print(f"  - {problem}", file=sys.stderr)
            continue
        print(f"{PROG}: {path} is a sound ticket")
    return status


def _composed(arguments: argparse.Namespace) -> int:
    """Print the follow-up agent's task, for the `compose` command."""
    root: Path = arguments.root
    try:
        template = arguments.template.read_text(encoding="utf-8")
        feedback = None if arguments.feedback is None else arguments.feedback.read_text("utf-8")
        task = compose(
            template,
            run=arguments.run,
            board=arguments.board,
            drafts_root=root,
            validate=arguments.validate,
            board_status=arguments.board_status,
            checkout=arguments.checkout,
            feedback=feedback,
            redispatch=feedback is not None or inventory(root, arguments.run)[1] > 0,
        )
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    sys.stdout.write(task)
    return SOUND


def _placed(arguments: argparse.Namespace) -> int:
    """Print the status a ticket is copied with, for the `board-status` command."""
    try:
        run, root_cause = located_path(arguments.path.absolute())
        ticket = qualified_id(run, root_cause)
        owner = board_owner(arguments.board)
        if owner is not None and not under_owner(repository := ticket_repository(ticket), owner):
            raise OutsideOwner(repository, owner)
        held = board_category(ticket, arguments.board)
        status = status_before_copy(held, withdraw=arguments.withdraw)
    except OutsideOwner as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return OUTSIDE_OWNER
    except Unplaced as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return UNPLACED
    except AcceptedOnBoard as refusal:
        print(f"{PROG}: {arguments.path}: {refusal}", file=sys.stderr)
        return ACCEPTED
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    print(status.written)
    return SOUND


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, decide a status, count or compose, for the recipe and the follow-up agent."""
    arguments = _parser().parse_args(argv)
    match arguments.command:
        case "validate":
            return _validated(arguments.paths)
        case "board-status":
            return _placed(arguments)
        case _ if not RECORD_COMPONENT.fullmatch(arguments.run):
            print(f"{PROG}: refused: {arguments.run!r} is not a run id", file=sys.stderr)
            return UNRUNNABLE
        case "inventory":
            held_drafts, held_tickets = inventory(arguments.root, arguments.run)
            print(f"{held_drafts} {held_tickets}")
            return SOUND
        case "check-run":
            tickets = (arguments.root / TASKS_DIRECTORY / arguments.run / TICKETS).glob("*.md")
            return _validated(sorted(tickets))
        case _:
            return _composed(arguments)


if __name__ == "__main__":  # pragma: no cover - the module's own command line
    raise SystemExit(main())
