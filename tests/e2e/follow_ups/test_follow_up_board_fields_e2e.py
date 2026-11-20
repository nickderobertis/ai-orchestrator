"""onefollowup's tickets on the board's fields, through the real store, on both wires.

The adopted onefollowup writes a ticket record carrying every value `onetaskgraph.yaml`'s
`followups` source projects onto the board's seven text fields, and a kind's `projections`
render more into the record's `projected`. One journey per destination, each driven with the
real `onefollowup` and the installed plan store over a store of the journey's own, its only
stand-ins the loopback wires: the GitHub board `tests/github_board.py` serves, and the Linear
team `tests/linear_board.py` serves, which the journey's store routes every ticket of the
owner's repositories to. That store is this checkout's `followups` source with its seven
`metadata_fields` and one more reading `projected.who_is_affected`, which the journey's own
`root-cause` kind — the shipped one with one `projections` entry — renders.

* **A new ticket's values reach the fields, and none of them its body**: a batch files one
  ticket in the shipped shape; on GitHub its item holds each record value on its field, as
  GitHub answers the store's reads, and on Linear its stored description ends with one
  collapsed `Fields` section holding them, which a store read of its content does not return.
* **A ticket written at schema 15 keeps its fields and its body through two operations**:
  onetaskgraph#4039 as it stands on the `followups` board (its record relabelled 14 with every
  other key as there, and its body), written by the store onto a card its owning run filed, so
  that run's receipt binds it, is re-copied bound by that run, then given a later run's
  evidence and re-estimate under a route that moves its priority and assignee. Every field
  holds its value from #4039's record and every visible body part holds through both, the
  one-unit fix's opening and the estimate line apart, and the priority and assignee read back
  as the re-estimate set them. Two known gaps are pinned beside it: on Linear the bound re-copy
  is refused, the card having been written since onefollowup's receipt; and #4039's real card
  has no such receipt, so its owning run's re-copy lands as a second card.

Hooks are switched off with onefollowup's own `--set`; the verifying journey beside this one
drives the configuration unmodified.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `xdist_group` selects an xdist
worker under this project's `--dist loadgroup`, keeping each module-scoped wire on one worker;
it selects no test tier.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

import follow_up_store
import pytest
import scripted_agent
import yaml
from github_board import (
    BOARD,
    FOLLOWUPS_ENV_PREFIX,
    FOLLOWUPS_OPTIONS,
    FOLLOWUPS_PROJECT_NUMBER,
    PLAN_STORE_REPOSITORY,
    PROJECTION_FIELD_NAME,
    SIBLING_REPOSITORY,
    _hosted,
    _Issue,
    _serving_board,
)
from linear_board import MEASURED_REWRITES, LinearBoard, linear_board, linear_stored
from scratch_identity import seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

ONEFOLLOWUP = REPO_ROOT / ".venv" / "bin" / "onefollowup"
ONETASKGRAPH = REPO_ROOT / ".venv" / "bin" / "onetaskgraph"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "follow-ups"
#: onetaskgraph#4039 as the `followups` board holds it, and the body its agent authors.
BOARD_TICKET = FIXTURES / "onetaskgraph-4039.md"
AUTHORED = FIXTURES / "onetaskgraph-4039.authored.md"

#: The two destinations the store's `followups` resolves to: the GitHub board itself, or the
#: Linear team its route sends the owner's repositories to.


class Destination(StrEnum):
    """Which wire the store's follow-up destination is."""

    GITHUB = "github"
    LINEAR = "linear"


GITHUB, LINEAR = Destination.GITHUB, Destination.LINEAR
LINEAR_SOURCE = "followups-linear"
LINEAR_KEY_ENV = "BOARD_FIELDS_LINEAR_API_KEY"

#: The batch filing a new ticket, the repository it is about, and the later run's batch.
NEW_BATCH = "board-fields-batch"
NEW_NODE = "paging"
ABOUT = _hosted(SIBLING_REPOSITORY)
PLAN_STORE = _hosted(PLAN_STORE_REPOSITORY)
LATER = "board-fields-later-run"
HOST = "verifier-board-fields"
SESSION = "e2e-board-fields"
DRAFT_BODY = (
    "## What happened\nA reader missed results.\n\n## Where\n`src/cursor.py`.\n\n"
    "## Why it is out of scope\nThe run was about something else.\n\n"
    "## Evidence\nSeen in the run's own log.\n"
)

#: The seven fields the checkout's `followups` source projects the record onto, and the path
#: each reads; then the one this journey's store adds, reading the kind's projection.
SEVEN = (
    ("Host", ("host",)),
    ("Owning runs", ("owning_runs",)),
    ("Basis", ("basis",)),
    ("Drafts", ("draft_sources",)),
    ("Transcripts", ("transcripts",)),
    ("Witnesses", ("witnesses",)),
    ("Verified at", ("verified_at",)),
)
PROJECTION = "who_is_affected"
FIELDS = (*SEVEN, (PROJECTION_FIELD_NAME, ("projected", PROJECTION)))
#: The kind's one projection: the `Who is affected` part of a ticket's `## Impact`.
PROJECTION_TEMPLATE = (
    "{% for part in ticket.section('Impact').parts if part.label == 'Who is affected' %}"
    "{{ part.text }}{% endfor %}"
)

#: The record keys onefollowup writes itself, which an authored record leaves to it.
WRITTEN_BY_ONEFOLLOWUP = frozenset(
    {"draft_sources", "transcripts", "priority_estimate", "board_estimate", "projected"}
)

#: The outcome a store's conditional write is refused with when the item moved since its read.
VERSION_MOVED = "version-moved"

#: Who the later run's route assigns an onetaskgraph ticket to, and the priority it sets.
ASSIGNEE = "bob"
ROUTED_PRIORITY = "high"

#: The store's metadata slot on GitHub, and on Linear the collapsed metadata block and the
#: collapsed `Fields` section standing directly before it, as each stores them: the section
#: is the one `+++ Fields` block followed by a metadata block opening with the
#: `onetaskgraph.fields` sentinel, as onetaskgraph's `docs/metadata.md` states under "On
#: Linear: one collapsed `Fields` section".
GITHUB_SLOT = re.compile(r"\n*<!-- onetaskgraph\.metadata\n.*?\n-->\n*", re.DOTALL)
LINEAR_SLOT = re.compile(
    r"\n*\+\+\+ onetaskgraph metadata\n\n(?:`onetaskgraph\.fields`\n\n)?`\{[^\n]*\}`\n\n\+\+\+\n*"
)
FIELDS_SECTION = re.compile(
    r"\n*\+\+\+ Fields\n\n(?P<section>(?:(?!\n\+\+\+\n).)*)\n\n\+\+\+\n\n"
    r"(?=\+\+\+ onetaskgraph metadata\n\n`onetaskgraph\.fields`\n)",
    re.DOTALL,
)
ESTIMATE_LINE = re.compile(r"^(?P<bullet>[-*]) Priority estimate: .*$", re.MULTILINE)


class Read(NamedTuple):
    """One item as the store reads it back, with what its destination shows beside it."""

    title: str
    status: str
    body: str
    priority: str | None
    assignees: list[str]
    fields: dict[str, str]
    stored: str


class Journey(NamedTuple):
    """One destination once every operation ran."""

    destination: Destination
    new_record: dict[str, object]
    new: Read
    seeded_record: dict[str, object]
    seeded: Read
    recopy_refused: list[str]
    recopied: Read
    reestimated: Read
    later_record: dict[str, object]


def _front(path: Path) -> tuple[dict[str, object], str]:
    """A fixture's front matter and its body."""
    _, front, body = path.read_text(encoding="utf-8").split("---\n", 2)
    held: dict[str, object] = yaml.safe_load(front)
    return held, body


def _shown(value: object) -> str:
    """A record value as the store projects it onto a text field: a string as it is, a list
    one line per element, a map one `KEY: VALUE` line per entry in byte order of its keys."""
    match value:
        case str():
            return value
        case list():
            return "\n".join(str(element) for element in value)
        case dict():
            return "\n".join(f"{key}: {value[key]}" for key in sorted(value))
    raise AssertionError(f"no text field shows {value!r}")


def _at(record: Mapping[str, object], path: tuple[str, ...]) -> object:
    held: object = record
    for key in path:
        assert isinstance(held, dict), (path, record)
        held = held.get(key)
    return held


class Wired(NamedTuple):
    """Everything one journey's commands run with."""

    environment: dict[str, str]
    store: follow_up_store.Store
    destination: Destination
    tracker: LinearBoard | None


def _run(wired: Wired, argv: list[str], stdin: str = "") -> dict[str, object]:
    ran = subprocess.run(  # noqa: S603 - the pinned onefollowup and plan store
        argv,
        cwd=wired.store.directory,
        env=wired.environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, f"{argv} exited {ran.returncode}: {ran.stdout}{ran.stderr}"
    answer: dict[str, object] = json.loads(ran.stdout)
    return answer


def _onefollowup(wired: Wired, config: Path, *arguments: str, stdin: str = "") -> dict[str, object]:
    """One `onefollowup` verb over the journey's configuration, hooks switched off."""
    return _run(
        wired,
        [str(ONEFOLLOWUP), "--config", str(config), "--set", "hooks.batch_preparing=null"]
        + ["--set", "hooks.batch_settled=null", *arguments],
        stdin,
    )


def _configuration(
    root: Path, wired: Wired, name: str, routes: list[dict[str, object]], without: str = ""
) -> Path:
    """This checkout's `onefollowup.yaml` with the shipped `root-cause` kind and one
    `projections` entry, ``routes``, and the shipped checks less ``without``."""
    shown = _run(wired, [str(ONEFOLLOWUP), "config", "show"])["config"]
    assert isinstance(shown, dict), shown
    (kind,) = [kind for kind in shown["kinds"] if kind["name"] == shown["default_kind"]]
    checks = [check for check in kind["checks"] if check.get("check") != without]
    document = yaml.safe_load((REPO_ROOT / "onefollowup.yaml").read_text(encoding="utf-8"))
    document["kinds"] = [
        kind | {"checks": checks, "projections": {PROJECTION: PROJECTION_TEMPLATE}}
    ]
    document["routes"] = routes
    path = root / f"onefollowup.{name}.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _metadata_fields(key: str) -> list[dict[str, object]]:
    return [{"field": name, "key": key, "path": list(path)} for name, path in FIELDS]


def _store_configuration(
    store: follow_up_store.Store, destination: Destination
) -> dict[str, object]:
    """The journey's store: `followups` as this checkout declares it, its seven fields and the
    projection's, assignees written; for Linear, a team's source carrying the same."""
    path = store.directory / "onetaskgraph.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = document["sources"]
    followups = sources["followups"]
    (key,) = {str(entry["key"]) for entry in followups["config"]["metadata_fields"]}
    declared = [
        (entry["field"], tuple(entry["path"])) for entry in followups["config"]["metadata_fields"]
    ]
    assert declared == list(SEVEN), declared
    followups["config"]["metadata_fields"] = _metadata_fields(key)
    followups["config"]["assignees"] = True
    drafts = sources[
        next(name for name in sources if name not in ("followups", follow_up_store.DESTINATION))
    ]
    drafts["config"]["assignees"] = True
    if destination == LINEAR:
        sources[LINEAR_SOURCE] = {
            "visibility": "public",
            "plugin": "linear",
            "config": {
                "api_key_env": LINEAR_KEY_ENV,
                "team": "ENG",
                "project": "0f3c9d2a-5b1e-4c7a-9e21-6d8b4a1f2c03",
                "status_mapping": {
                    "backlog": "Proposal",
                    "draft": "Deferred",
                    "todo": "Todo",
                    "queued": "Queued",
                    "in-progress": "In Progress",
                    "unknown": "Needs Attention",
                    "done": "Done",
                    "cancelled": "Cancelled",
                },
                "metadata_fields": _metadata_fields(key),
                "assignees": True,
            },
        }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return document


def _plant(store: follow_up_store.Store, native: str, front: dict[str, object], body: str) -> None:
    """One item of the local drafts source, as the store writes one."""
    path = store.drafts / "tasks" / f"{native}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{yaml.safe_dump(front, sort_keys=False)}---\n{body}", encoding="utf-8")


class OwningRun(NamedTuple):
    """The run that filed #4039, the draft its record names, and the ticket's root cause."""

    run: str
    draft: str
    cause: str


def _owning_run(store: follow_up_store.Store) -> OwningRun:
    """onetaskgraph#4039's owning run as the drafts source holds it: its project and its draft
    at the id the ticket's record names."""
    held, _ = _front(BOARD_TICKET)
    metadata = held["metadata"]
    assert isinstance(metadata, dict)
    record = metadata["orchestrator.follow-up"]
    run, cause = str(record["created_by_run"]), str(record["root_cause"])
    (draft,) = record["drafts"]
    _plant(
        store,
        f"../projects/{run}",
        {
            "title": run,
            "status": "todo",
            "metadata": {"orchestrator.follow-up-drafts": {"schema": 1, "run": run}},
        },
        "",
    )
    author = record["draft_sources"][draft]
    node = author.rsplit("node ", 1)[1]
    _plant(
        store,
        draft.split(":", 1)[1],
        {
            "title": "A projected metadata field with no value costs extra board reads",
            "status": "todo",
            "project": run,
            "metadata": {
                "orchestrator.follow-up-draft": {
                    "schema": 1,
                    "run": run,
                    "author": {"kind": "node", "node": node, "member": None},
                    "dispatch": {
                        "scratch": None,
                        "session": None,
                        "cwd": str(REPO_ROOT),
                        "branch": None,
                        "head": None,
                    },
                    "transcript": record["transcripts"][draft],
                    "repository": record["repository"],
                    "paths": [],
                    "drafted_at": "2026-11-14T00:34:47Z",
                }
            },
            "repositories": [record["repository"]],
        },
        DRAFT_BODY,
    )
    return OwningRun(run, str(draft), cause)


def _item_id(wired: Wired, origin: str) -> str:
    """The destination item a copy of ``origin`` landed on, by the copy link the store kept."""
    shown = _run(wired, [str(ONETASKGRAPH), "--no-interactive", "task", "show", origin, "--json"])
    item = shown["items"][0]["item"]
    links = item["metadata"]["onetaskgraph.copies"]
    (link,) = links if isinstance(links, list) else list(links.values())
    return str(link["id"] if isinstance(link, dict) else link)


def _read(wired: Wired, item: str) -> Read:
    """``item`` as the store reads it back, and what its destination shows beside it."""
    shown = _run(wired, [str(ONETASKGRAPH), "--no-interactive", "task", "show", item, "--json"])
    task = shown["items"][0]["item"]
    body = str(task.get("content") or "")
    assignees = [str(person) for person in task.get("assignees") or []]
    priority = task.get("priority")
    held = (str(task["title"]), json.dumps(task.get("status"), sort_keys=True))
    if wired.tracker is None:
        (issue,) = [issue for issue in BOARD.issues if str(issue.content_id) in item]
        return Read(*held, body, priority, assignees, _github_fields(issue), issue.body)
    native = item.split(":", 1)[1]
    (stored,) = [one for one in wired.tracker.issues() if one["id"] == native]
    description = str(stored["description"])
    return Read(*held, body, priority, assignees, _linear_fields(description), description)


def _github_fields(issue: _Issue) -> dict[str, str]:
    """Each text field's value on the item, as GitHub answers every read of it."""
    return {
        str(node["field"]["name"]): str(node["text"])
        for node in issue.field_values()["nodes"]
        if "text" in node and node["field"]["name"] != "onetaskgraph.origin"
    }


def _unspanned(text: str) -> str:
    """One inline code span's text, as the Linear store writes a value into one."""
    spanned = re.fullmatch(r"(`+) ?(.*?) ?\1", text.strip())
    assert spanned is not None, text
    return spanned[2]


def _linear_fields(description: str) -> dict[str, str]:
    """The values the stored description's collapsed `Fields` section shows, by field: one
    bullet a field, its value a code span, or one nested bullet a line."""
    (section,) = [found["section"] for found in FIELDS_SECTION.finditer(description)]
    held: dict[str, list[str]] = {}
    name = None
    for line in section.splitlines():
        if found := re.match(r"^[-*] \*\*(?P<name>[^*]+)\*\*: ?(?P<value>.*)$", line):
            name = found["name"]
            held[name] = [_unspanned(found["value"])] if found["value"].strip() else []
        elif name is not None and (nested := re.match(r"^\s+[-*] (?P<value>.*)$", line)):
            held[name].append(_unspanned(nested["value"]))
    return {field: "\n".join(lines) for field, lines in held.items()}


def _visible(body: str) -> str:
    """A body with the store's slot taken off, every section, part and line a person reads."""
    return GITHUB_SLOT.sub("\n", LINEAR_SLOT.sub("\n", body)).strip()


def _new_ticket(wired: Wired, config: Path) -> tuple[dict[str, object], str]:
    """A node's follow-up draft, filed as a ticket in the shipped shape.

    Drafted as `scripts/follow-up-draft.sh` drafts for a node, its author and its transcript
    stamped, so the record's `draft_sources` and `transcripts` both hold it.
    """
    title = "Paging drops a row"
    draft = str(
        _onefollowup(
            wired,
            config,
            "draft",
            "--batch",
            NEW_BATCH,
            "--stamp",
            f"transcript=onepipeline transcript {NEW_BATCH} {NEW_NODE}",
            "--author",
            f"node:{NEW_NODE}",
            "--title",
            title,
            "--repository",
            ABOUT,
            stdin=DRAFT_BODY,
        )["draft"]
    )
    _onefollowup(wired, config, "prepare", "--batch", NEW_BATCH)
    tickets = wired.store.work / "tasks" / NEW_BATCH / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    (tickets / f"{scripted_agent.NEW_ROOT_CAUSE}.md").write_text(
        scripted_agent._ticket(NEW_BATCH, ABOUT, HOST, draft, title), encoding="utf-8"
    )
    _onefollowup(
        wired, config, "ticket", "copy", "--batch", NEW_BATCH, scripted_agent.NEW_ROOT_CAUSE
    )
    _dispose(wired, NEW_BATCH, [(draft, scripted_agent.NEW_ROOT_CAUSE)])
    _publish(wired, config, NEW_BATCH)
    (card,) = _cards(wired, NEW_BATCH)
    return _record_of(wired, card), card


def _cards(wired: Wired, batch: str) -> list[str]:
    """The destination item each ticket copy of ``batch`` landed on, as its receipts name it."""
    receipts = (wired.store.work / "receipts" / f"{batch}.jsonl").read_text("utf-8")
    return [
        str(line["card"])
        for line in map(json.loads, receipts.splitlines())
        if line.get("command") == "copy" and line.get("card")
    ]


def _record_of(wired: Wired, item: str) -> dict[str, object]:
    shown = _run(wired, [str(ONETASKGRAPH), "--no-interactive", "task", "show", item, "--json"])
    record: dict[str, object] = shown["items"][0]["item"]["metadata"]["orchestrator.follow-up"]
    return record


def _dispose(wired: Wired, batch: str, filed: list[tuple[str, str]]) -> None:
    answers = wired.store.work / "dispositions" / f"{batch}.json"
    held = json.loads(answers.read_text(encoding="utf-8"))
    held["dispositions"] = [
        {
            "draft": draft,
            "disposition": "filed",
            "root_causes": [cause],
            "detail": "Verified at the basis.",
            "kind": "root-cause",
        }
        for draft, cause in filed
    ]
    answers.write_text(json.dumps(held, indent=2) + "\n", encoding="utf-8")


def _publish(
    wired: Wired, config: Path, batch: str, expected: frozenset[str] = frozenset()
) -> list[str]:
    """Publish and settle ``batch``; the outcomes it refused, each one of ``expected``."""
    assert _onefollowup(wired, config, "check", "--batch", batch)["sound"] is True
    sent = _onefollowup(wired, config, "publish", "--batch", batch)
    outcomes = sent["outcomes"]
    assert isinstance(outcomes, list), sent
    refused = [
        outcome
        for outcome in outcomes
        if outcome["outcome"] not in ("created", "updated", "written", "unchanged")
    ]
    assert all(outcome["outcome"] in expected for outcome in refused), (
        refused,
        (wired.store.work / "receipts" / f"{batch}.jsonl").read_text("utf-8"),
    )
    _onefollowup(wired, config, "settle", "--batch", batch)
    return [str(outcome["outcome"]) for outcome in refused]


def _corrected(authored: str) -> str:
    """#4039 as its owning run's agent corrects it to the shipped shape: its one-unit fix
    without the opening paragraph `fix-units` refuses, which the unit's own opening says."""
    head, fix = authored.split("## Suggested fix\n\n", 1)
    opening, rest = fix.split("**Contributing locations.**", 1)
    assert opening.strip() and "\n\n" not in opening.strip(), opening
    return f"{head}## Suggested fix\n\n**Contributing locations.**{rest}"


def _fix_opening(body: str) -> str:
    """The paragraph opening a body's `## Suggested fix`, before its locations block."""
    return body.split("## Suggested fix\n\n", 1)[1].split("\n\n<details>", 1)[0]


def _file(
    wired: Wired,
    config: Path,
    run: str,
    cause: str,
    authored: str,
    expected: frozenset[str] = frozenset(),
) -> list[str]:
    """The owning run copying its ticket for ``cause``, its body ``authored``, and publishing it:
    a create the first time, and a copy bound to that card by the run's journal after. Answers
    the outcomes publishing refused, each one of ``expected``."""
    held, _ = _front(BOARD_TICKET)
    metadata = held["metadata"]
    assert isinstance(metadata, dict)
    record = {
        key: value
        for key, value in metadata["orchestrator.follow-up"].items()
        if key not in WRITTEN_BY_ONEFOLLOWUP
    }
    _onefollowup(wired, config, "prepare", "--batch", run)
    tickets = wired.store.work / "tasks" / run / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    (tickets / f"{cause}.md").write_text(
        f"---\ntitle: {json.dumps(held['title'])}\nmetadata:\n  orchestrator.follow-up: "
        f"{json.dumps(record)}\n---\n{authored}",
        encoding="utf-8",
    )
    _onefollowup(wired, config, "ticket", "copy", "--batch", run, cause)
    (draft,) = record["drafts"]
    _dispose(wired, run, [(str(draft), cause)])
    return _publish(wired, config, run, expected)


def _board_record() -> dict[str, object]:
    """#4039's `orchestrator.follow-up` record as the `followups` board holds it."""
    held, _ = _front(BOARD_TICKET)
    metadata = held["metadata"]
    assert isinstance(metadata, dict)
    record: dict[str, object] = metadata["orchestrator.follow-up"]
    return record


def _as_the_board_holds_it(wired: Wired, item: str) -> None:
    """Write ``item``'s body and record, through the store, to #4039's as the board holds them:
    the record at schema 14 with every other key as it stands there, `draft_sources`,
    `transcripts` and the estimates included."""
    _, body = _front(BOARD_TICKET)
    content = wired.store.directory / "onetaskgraph-4039.body.md"
    content.write_text(body, encoding="utf-8")
    cli = [str(ONETASKGRAPH), "--no-interactive", "task"]
    _run(wired, [*cli, "update", item, "--body-file", str(content), "--json"])
    _run(
        wired,
        [*cli, "metadata", "set", item, "orchestrator.follow-up", json.dumps(_board_record())]
        + ["--json"],
    )


def _reestimate(wired: Wired, config: Path, item: str, cause: str) -> None:
    """A later run's evidence on the item, and its re-estimate under the routed config."""
    draft = str(
        _onefollowup(
            wired,
            config,
            "draft",
            "--batch",
            LATER,
            "--author",
            "manager",
            "--title",
            "empty metadata projections must not reread board fields",
            "--repository",
            PLAN_STORE,
            stdin=DRAFT_BODY,
        )["draft"]
    )
    _onefollowup(wired, config, "prepare", "--batch", LATER)
    # The agent finds the earlier ticket for its root cause and reads it.
    found = _onefollowup(
        wired, config, "ticket", "search", "--batch", LATER, "--ticket", cause, "projections"
    )
    hits = found["hits"]
    assert isinstance(hits, list), found
    assert item in [str(hit["item"]) for hit in hits], found
    _onefollowup(wired, config, "ticket", "board-status", "--batch", LATER, item)
    provenance = wired.store.work / "tasks" / LATER / "evidence-sources.json"
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(scripted_agent.sources(HOST, PLAN_STORE, [draft]), encoding="utf-8")
    _onefollowup(
        wired,
        config,
        "ticket",
        "comment",
        "--batch",
        LATER,
        "--item",
        item,
        "--root-cause",
        cause,
        "--sources-file",
        str(provenance),
        stdin="A metadata write with no transcript read the board's fields again.\n",
    )
    _onefollowup(wired, config, "ticket", "re-estimate", "--batch", LATER, item)
    _dispose(wired, LATER, [(draft, cause)])
    _publish(wired, config, LATER)


@contextmanager
def _wired(root: Path, destination: Destination) -> Iterator[Wired]:
    """The journey's store and configuration, its fields set up, over a live wire."""
    registry = seeded(root / "registry", public=(ABOUT, PLAN_STORE))
    store = follow_up_store.isolated(root)
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ONEFOLLOWUP_", FOLLOWUPS_ENV_PREFIX))
    }
    environment |= store.environment | registry.environment
    environment |= {
        "ONEVCS_HOME": str(registry.home),
        follow_up_store.DESTINATION_ENV: LINEAR_SOURCE if destination == LINEAR else "followups",
        "ONEFOLLOWUP_HOOKS__OWNER__SESSION": SESSION,
        "ONEFOLLOWUP_HOST_LABEL": HOST,
    }
    configured = _store_configuration(store, destination)
    with (
        _serving_board(options=FOLLOWUPS_OPTIONS, number=FOLLOWUPS_PROJECT_NUMBER) as remote,
        linear_board() as tracker,
    ):
        environment |= remote
        environment[f"{FOLLOWUPS_ENV_PREFIX}CONFIG__PACING__MIN_MUTATION_INTERVAL_MS"] = "0"
        if destination == LINEAR:
            prefix = f"sources.{LINEAR_SOURCE}.config."
            linear = configured["sources"][LINEAR_SOURCE]["config"]
            environment |= tracker.environment(
                LINEAR_SOURCE,
                {
                    prefix + "api_key_env": LINEAR_KEY_ENV,
                    prefix + "status_mapping": linear["status_mapping"],
                },
            )
        wired = Wired(environment, store, destination, tracker if destination == LINEAR else None)
        # Set up as the operator set up the live board, through the plan-store verb.
        for source in ("followups", *((LINEAR_SOURCE,) if destination == LINEAR else ())):
            _run(
                wired,
                [
                    str(ONETASKGRAPH),
                    "--no-interactive",
                    "sources",
                    "fields",
                    source,
                    "--apply",
                    "--json",
                ],
            )
        yield wired


def _journey(root: Path, destination: Destination) -> Iterator[Journey]:
    with _wired(root, destination) as wired:
        store = wired.store
        plain = _configuration(root, wired, "shipped", [])
        earlier = _configuration(root, wired, "earlier", [], without="fix-units")
        routed = _configuration(
            root,
            wired,
            "routed",
            [
                {
                    "name": "plan-store-owner",
                    "match": {"repository": [PLAN_STORE]},
                    "set": {"priority": ROUTED_PRIORITY, "assignee": ASSIGNEE},
                }
            ],
        )

        # onetaskgraph#4039's card, filed by its owning run under the shipped kind less the
        # `fix-units` check, which 0.7.0 added after this ticket was written and which refuses
        # the summary opening its one-unit fix: the create gives the card the receipt a re-copy
        # binds by. Then the store writes the card's body and record to exactly what the board
        # holds, so the baseline is #4039 itself, not a record onefollowup regenerated.
        owning = _owning_run(store)
        run, cause = owning.run, owning.cause
        authored = AUTHORED.read_text(encoding="utf-8").split("-->\n", 1)[1]
        _file(wired, earlier, run, cause, authored)
        (seeded_item,) = _cards(wired, run)
        _as_the_board_holds_it(wired, seeded_item)
        seeded_record = _record_of(wired, seeded_item)
        seeded_read = _read(wired, seeded_item)

        _, new_item = _new_ticket(wired, plain)
        new_read = _read(wired, new_item)

        # Its owning run's re-copy under the shipped kind, the ticket corrected to pass it. On
        # Linear it is refused: the known gap the case below pins.
        refused = _file(
            wired,
            plain,
            run,
            cause,
            _corrected(authored),
            frozenset({VERSION_MOVED}) if destination == LINEAR else frozenset(),
        )
        recopied = _read(wired, seeded_item)
        _reestimate(wired, routed, seeded_item, cause)
        reestimated = _read(wired, seeded_item)
        yield Journey(
            destination,
            _record_of(wired, new_item),
            new_read,
            seeded_record,
            seeded_read,
            refused,
            recopied,
            reestimated,
            _record_of(wired, seeded_item),
        )


@pytest.fixture(scope="module", params=[GITHUB, LINEAR])
def journey(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Journey]:
    yield from _journey(tmp_path_factory.mktemp(f"board-fields-{request.param}"), request.param)


@pytest.mark.xdist_group("board-fields")
def test_a_new_ticket_shows_every_record_value_and_the_projection_on_the_board(
    journey: Journey,
) -> None:
    record = journey.new_record
    projected = record.get("projected")
    assert projected == {PROJECTION: "Readers of long listings."}, record
    for name, path in FIELDS:
        value = _at(record, path)
        assert value, (name, record)
        assert journey.new.fields.get(name) == _shown(value), (name, journey.new.fields)


@pytest.mark.xdist_group("board-fields")
def test_a_new_ticket_s_body_shows_none_of_its_metadata_values(journey: Journey) -> None:
    visible = _visible(journey.new.body)
    for name, path in FIELDS[:-1]:
        for line in _shown(_at(journey.new_record, path)).splitlines():
            assert line not in visible, (name, line, visible)
    assert "## Owning batches" not in visible and "## Owning runs" not in visible, visible


@pytest.mark.xdist_group("board-fields")
def test_on_linear_the_fields_are_one_collapsed_section_a_store_read_leaves_out(
    journey: Journey,
) -> None:
    if journey.destination != LINEAR:
        assert FIELDS_SECTION.search(journey.new.stored) is None
        return
    stored = journey.new.stored
    (section,) = FIELDS_SECTION.finditer(stored)
    assert section.start() > stored.index("## Evidence"), stored
    assert LINEAR_SLOT.search(stored, section.end()), stored
    assert "<!--" not in stored, stored
    assert FIELDS_SECTION.search(journey.new.body) is None
    assert "Fields" not in journey.new.body.rsplit("## Evidence", 1)[1], journey.new.body


@pytest.mark.xdist_group("board-fields")
def test_the_seeded_ticket_stands_on_the_destination_as_the_board_holds_it(
    journey: Journey,
) -> None:
    _, body = _front(BOARD_TICKET)
    # Linear keeps a long form as it rewrites it, which a read answers as kept.
    shown = body if journey.destination == GITHUB else linear_stored(body, MEASURED_REWRITES)
    assert _visible(journey.seeded.body) == shown.strip()
    record = _board_record()
    assert record["schema"] == 14, record
    assert journey.seeded_record == record
    for name, path in SEVEN:
        assert _at(record, path), (name, record)
        assert journey.seeded.fields.get(name) == _shown(_at(record, path)), name


def _held_fields(read: Read) -> None:
    """Each of the seven fields still holds the value #4039's own record gives it."""
    record = _board_record()
    for name, path in SEVEN:
        assert read.fields.get(name) == _shown(_at(record, path)), (name, read.fields)


def _without_estimate(body: str) -> str:
    return ESTIMATE_LINE.sub(r"\g<bullet> Priority estimate: <re-estimated>", _visible(body))


@pytest.mark.xdist_group("board-fields")
def test_a_bound_recopy_drops_only_the_one_unit_fix_s_opening_paragraph(journey: Journey) -> None:
    """Under the shipped kind, `fix-units` on, the owning run's re-copy lands its corrected
    ticket: the fix loses the summary it said twice, and nothing else moves."""
    if journey.destination == LINEAR:
        pytest.skip("refused on Linear: the known gap the case below pins")
    seeded, recopied = journey.seeded, journey.recopied
    assert journey.recopy_refused == []
    opening = _fix_opening(_visible(seeded.body))
    assert opening and not opening.startswith("<details>"), opening
    assert _fix_opening(_visible(recopied.body)).startswith("<details>"), recopied.body
    expected = _without_estimate(seeded.body).replace(f"{opening}\n\n<details>", "<details>", 1)
    assert _without_estimate(recopied.body) == expected
    assert (recopied.title, recopied.status) == (seeded.title, seeded.status)
    assert (recopied.priority, recopied.assignees) == (seeded.priority, seeded.assignees)
    _held_fields(recopied)


@pytest.mark.xdist_group("board-fields")
def test_a_later_re_estimate_moves_priority_and_assignee_and_keeps_the_rest(
    journey: Journey,
) -> None:
    recopied, reestimated = journey.recopied, journey.reestimated
    _held_fields(reestimated)
    assert _without_estimate(reestimated.body) == _without_estimate(recopied.body)
    assert (reestimated.title, reestimated.status) == (recopied.title, recopied.status)
    assert recopied.priority != ROUTED_PRIORITY, recopied
    assert reestimated.priority == ROUTED_PRIORITY, reestimated
    assert recopied.assignees == []
    assert reestimated.assignees == [ASSIGNEE], reestimated
    assert journey.later_record.get("board_assignee") == ASSIGNEE, journey.later_record


@pytest.mark.xdist_group("board-fields")
def test_known_gap_on_linear_a_bound_recopy_of_a_card_written_since_its_receipt_is_refused(
    journey: Journey,
) -> None:
    """The known gap the drafted onefollowup follow-up names: a bound re-copy writes on the
    condition that the card is still at the version onefollowup's receipt recorded, and Linear
    holds a write to that condition, so a card anything else wrote since — a person, or the
    relabel that brought #4039's record to schema 14 — is refused `version-moved` and keeps
    every field and body part it had. GitHub's store reads before it writes and takes no
    condition. This fails once onefollowup re-reads the card before its bound write; drop the
    skip above then and let the case above hold Linear too."""
    if journey.destination != LINEAR:
        assert journey.recopy_refused == []
        return
    assert journey.recopy_refused == [VERSION_MOVED]
    seeded, recopied = journey.seeded, journey.recopied
    assert recopied == seeded
    _held_fields(recopied)


def _pre_cutover(root: Path) -> list[_Issue]:
    """#4039 as it stands on the board, its card copied by the store from the drafts item the
    pre-cutover module wrote at `<run>/tickets/<root cause>`, which the card's origin names,
    and its owning run re-copying it through onefollowup."""
    with _wired(root, GITHUB) as wired:
        plain = _configuration(root, wired, "shipped", [])
        owning = _owning_run(wired.store)
        run, cause = owning.run, owning.cause
        held, body = _front(BOARD_TICKET)
        metadata = held["metadata"]
        assert isinstance(metadata, dict)
        record = metadata["orchestrator.follow-up"]
        origin = str(held["origin"])
        path = wired.store.drafts / "tasks" / f"{origin.split(':', 1)[1]}.md"
        front = {
            "title": held["title"],
            "status": "backlog",
            "priority": record["board_estimate"],
            "project": run,
            "metadata": {"orchestrator.follow-up": record},
            "repositories": [record["repository"]],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{yaml.safe_dump(front, sort_keys=False)}---\n{body}", "utf-8")
        _run(
            wired,
            [str(ONETASKGRAPH), "--no-interactive", "task", "copy", origin, "--to", "followups"]
            + ["--json"],
        )
        authored = AUTHORED.read_text(encoding="utf-8").split("-->\n", 1)[1]
        _file(wired, plain, run, cause, _corrected(authored))
        return [issue for issue in BOARD.issues if issue.title == held["title"]]


@pytest.fixture(scope="module")
def pre_cutover(tmp_path_factory: pytest.TempPathFactory) -> list[_Issue]:
    return _pre_cutover(tmp_path_factory.mktemp("board-fields-pre-cutover"))


@pytest.mark.xdist_group("board-fields")
def test_known_gap_a_pre_cutover_ticket_re_copied_by_its_owning_run_lands_as_a_second_card(
    pre_cutover: list[_Issue],
) -> None:
    """The known gap the drafted onefollowup follow-up names: onefollowup binds a re-copy to
    its card only through its own receipt journal, so a ticket the pre-cutover module filed —
    whose card's origin is `drafts:<run>/tickets/<root>`, with no receipt — is copied again as
    a new card beside it. This fails once onefollowup binds by the card's origin or record;
    update it then to hold one card."""
    assert len(pre_cutover) == 2, [issue.content_id for issue in pre_cutover]
