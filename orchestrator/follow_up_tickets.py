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
this module's `validate` command before copying it. And the journeys read the board back
through :func:`from_store_item` and :func:`comment_owner`.

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
#: that outlives the agent that wrote it.
SCHEMA = 1

#: The metadata key a ticket's record sits under, which travels onto the board item.
KEY = "orchestrator.follow-up"


class Status(StrEnum):
    """A ticket's status: standing, or withdrawn by its run.

    The board holds a withdrawn ticket's issue as closed as not planned; no issue is ever
    deleted.
    """

    OPEN = "todo"
    WITHDRAWN = "cancelled"


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

#: A root cause's slug, which names the ticket's file.
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

#: A commit a claim was verified at.
COMMIT = re.compile(r"[0-9a-f]{40}")

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
VALUES = ("RUN", "BOARD", "DRAFTS_ROOT", "VALIDATE", "CHECKOUT")
SECTIONS = ("TICKET_CONTRACT", "COMMENT_CONTRACT", "REDISPATCH", "FEEDBACK")
PLACEHOLDERS = (*VALUES, *SECTIONS)

#: This command's name in its diagnostics.
PROG = "follow-up-tickets"

#: Exit statuses: every ticket is sound; a ticket failed the shape; the command could not run.
SOUND = 0
UNSOUND = 1
UNRUNNABLE = 2


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
    }


def render(ticket: Ticket) -> str:
    """One ticket as the `local-md` record it is stored as: no `project`, no `repositories`."""
    return frontmatter(
        {"title": ticket.title, "status": ticket.status, "metadata": {KEY: record(ticket)}},
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


def _record_problems(
    held: Mapping[str, object], *, run: str | None, root_cause: str | None
) -> list[str]:
    missing = [key for key in RECORD_KEYS if key not in held]
    if missing:
        return [f"the `{KEY}` record is missing {', '.join(missing)}"]
    found = []
    if unexpected := sorted(key for key in held if key not in RECORD_KEYS):
        found.append(
            f"the `{KEY}` record carries keys this does not write: {', '.join(unexpected)}"
        )
    if type(held["schema"]) is not int or held["schema"] != SCHEMA:
        found.append(f"the record is schema {held['schema']!r}, and this reads schema {SCHEMA}")
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
    return found


def _body_problems(body: object) -> list[str]:
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
        after = at + 1
    return []


def problems(
    item: Mapping[str, object], *, run: str | None = None, root_cause: str | None = None
) -> list[str]:
    """Every way a store item, as `onetaskgraph task show --json` reports it, is not a ticket.

    ``run`` and ``root_cause`` are what the ticket's path says, when it was read from one.
    """
    found = []
    if item.get("project") is not None:
        found.append(
            "the ticket carries a `project`; a ticket carries none, so it lands on the "
            "board as a standalone item"
        )
    if item.get("repositories"):
        found.append(
            "the ticket carries `repositories`; a ticket carries none, so its issue is "
            "filed in the board's own repository whatever repository the root cause lives in"
        )
    status = item.get("status")
    name = status.get("name") if isinstance(status, Mapping) else status
    if name not in tuple(Status):
        found.append(
            f"the status is {name!r}, where a ticket is `{Status.OPEN}` while it stands and "
            f"`{Status.WITHDRAWN}` once withdrawn"
        )
    metadata = item.get("metadata")
    held = metadata.get(KEY) if isinstance(metadata, Mapping) else None
    if isinstance(held, Mapping):
        found.extend(_record_problems(held, run=run, root_cause=root_cause))
        repository = held.get("repository")
    else:
        found.append(f"the ticket carries no `{KEY}` metadata record")
        repository = None
    found.extend(_title_problems(item.get("title"), repository))
    found.extend(_body_problems(item.get("content")))
    return found


def from_store_item(
    item: Mapping[str, object], *, run: str | None = None, root_cause: str | None = None
) -> Ticket:
    """The ticket a store item holds, or :class:`Refused` naming every problem."""
    found = problems(item, run=run, root_cause=root_cause)
    if found:
        raise Refused(found)
    metadata = item["metadata"]
    status = item["status"]
    # `problems` has just proven every shape narrowed here, and says so if it has not.
    assert isinstance(metadata, Mapping)  # noqa: S101
    held = metadata[KEY]
    assert isinstance(held, Mapping)  # noqa: S101
    basis = held["basis"]
    assert isinstance(basis, Mapping)  # noqa: S101
    return Ticket(
        title=str(item["title"]),
        status=Status(str(status.get("name") if isinstance(status, Mapping) else status)),
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
    "<per draft: the qualified draft id, its run and node, the verified claim with "
    "`path:line` at the basis commit, and the transcript command from the draft>",
    "<the suggested fixes>",
    "<every run whose evidence this ticket carries>",
)


def ticket_contract(run: str, board: str) -> str:
    """C5, as the follow-up agent is told it: the shape of the ticket it writes."""
    example = Ticket(
        title="<repository name>: <the root cause in one line>",
        status=Status.OPEN,
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
        "- It carries **no `project`**, so it lands on the board as a standalone item, and "
        "**no `repositories`**, so its issue is created in the board's own repository "
        "whatever repository the root cause lives in.\n"
        "- Its title is `<repository name>: <the root cause in one line>`, at most "
        f"{TITLE_LIMIT} characters, where the repository name is the last segment of "
        "`repository`.\n"
        f"- Its status is `{Status.OPEN}` while it stands. A ticket this run withdraws is "
        f"copied again with status `{Status.WITHDRAWN}`, which the board holds as closed as not "
        "planned. "
        "No issue is ever deleted.\n"
        f"- Its front matter carries the `{KEY}` record with every key present, and its body "
        f"the headings {headings}, in that order, each with content. `created_by_run` is "
        "this run, and `owning_runs` includes it.\n"
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
  joined by a second.
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
    count = commands.add_parser("inventory", help="print how many drafts and tickets a run holds")
    count.add_argument("--root", type=Path, required=True, help=root_help)
    count.add_argument("run", metavar="RUN-ID")
    task = commands.add_parser("compose", help="print the follow-up agent's task")
    task.add_argument("--template", type=Path, required=True)
    task.add_argument("--root", type=Path, required=True, help=root_help)
    task.add_argument("--run", required=True, metavar="RUN-ID")
    task.add_argument("--board", required=True, metavar="SOURCE")
    task.add_argument("--validate", required=True, metavar="COMMAND")
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
            checkout=arguments.checkout,
            feedback=feedback,
            redispatch=feedback is not None or inventory(root, arguments.run)[1] > 0,
        )
    except (OSError, Refused) as exc:
        print(f"{PROG}: refused: {exc}", file=sys.stderr)
        return UNRUNNABLE
    sys.stdout.write(task)
    return SOUND


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, count or compose, for `scripts/follow-ups.sh` and the follow-up agent."""
    arguments = _parser().parse_args(argv)
    match arguments.command:
        case "validate":
            return _validated(arguments.paths)
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
