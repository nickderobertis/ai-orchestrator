"""The engine's human-item verbs, as this host's planning and follow-up flows call them.

Everything about an item — its store, its hold, its priority derivation, its projection into
a tracker, its delivery to the owning session's watch — is the engine's
(`onepipeline human-items`, configured by `.onepipeline/config.yaml` and the host's own
override). This module only runs that verb through `scripts/onepipeline.sh`, the one wrapper
every engine verb here goes through, and reads its JSON answer back, so a flow raises an item
by naming a configured phase and a data-only context and nothing else.

**A host with human items off is not a failure.** The engine refuses every verb as
`{"kind":"unavailable"}` while the effective configuration leaves them disabled, and a flow
that would have raised an item goes on without one: :class:`Unavailable` is that answer, and
every caller reads it as "nothing to raise here" rather than as a broken step.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, NamedTuple, NewType

from orchestrator.plan_store import QualifiedProjectId
from orchestrator.root import REPO_ROOT

#: The wrapper every engine verb on this host runs through.
ENGINE = REPO_ROOT / "scripts" / "onepipeline.sh"

#: The one definition of who is acting, which the wrapper sources too.
LAUNCHER_SESSION = REPO_ROOT / "scripts" / "launcher-session.sh"

#: The words the engine's typed refusal carries while human items are off.
UNAVAILABLE = '"kind":"unavailable"'

#: The longest one verb may take: a raise or a completion projects a task into the configured
#: tracker and a completion runs the approval adapter, each a handful of requests.
TIMEOUT_SECONDS = 300

#: The engine's item kinds, states and wire priorities (onepipeline's `HumanItem`).
Kind = Literal["task", "question"]
State = Literal["open", "answered", "completed", "dismissed"]
Priority = Literal["urgent", "high", "medium", "low"]
KINDS: tuple[Kind, ...] = ("task", "question")
STATES: tuple[State, ...] = ("open", "answered", "completed", "dismissed")
PRIORITIES: tuple[Priority, ...] = ("urgent", "high", "medium", "low")


#: An item's id, as the engine mints it from its dedup key.
ItemId = NewType("ItemId", str)

#: A choice's id, unique within its question (onepipeline's `HumanItemChoice`).
ChoiceId = NewType("ChoiceId", str)

#: The schema's bounds on a question's choices: how many, and how an id and a title are spelled.
MAX_CHOICES = 6
CHOICE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
CHOICE_TITLE_LENGTH = 80
CHOICE_FIELDS = frozenset({"id", "title", "description"})

#: Every field the engine's `HumanItemResolution` declares; it declares no other.
RESOLUTION_FIELDS = frozenset({"actor", "on_behalf_of", "text", "choice", "reason", "provenance"})

#: Whose words a resolution is (onepipeline's `Provenance`), from onepipeline 0.110.0: the
#: manager's own or the user's relayed; omitted while it is the manager's.
PROVENANCES = ("manager", "user")

#: The document version a task stored: opaque to the engine, and for a design review the
#: approval key `orchestrator/design_approval.py` computes.
VersionKey = NewType("VersionKey", str)


class Choice(NamedTuple):
    """One answer a question offers: an id, a one-line title, and a markdown description."""

    id: ChoiceId
    title: str
    description: str


def _choices(value: object) -> tuple[Choice, ...]:
    """A record's ``choices`` as the schema allows them, or :class:`OSError` naming why not.

    Absent is no choices, because the engine omits the field when there are none.
    """
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_CHOICES:
        raise OSError(f"the engine answered an item whose choices are {value!r}")
    choices = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != CHOICE_FIELDS:
            raise OSError(f"the engine answered an item offering the choice {entry!r}")
        identifier, title, description = entry["id"], entry["title"], entry["description"]
        if (
            not isinstance(identifier, str)
            or CHOICE_ID.fullmatch(identifier) is None
            or not isinstance(title, str)
            or not title.strip()
            or "\n" in title
            or "\r" in title
            or len(title) > CHOICE_TITLE_LENGTH
            or not isinstance(description, str)
            or not description.strip()
        ):
            raise OSError(f"the engine answered an item offering the choice {entry!r}")
        choices.append(Choice(ChoiceId(identifier), title, description))
    if len({choice.id for choice in choices}) != len(choices):
        raise OSError(f"the engine answered an item offering one choice id twice: {value!r}")
    return tuple(choices)


class Approval(NamedTuple):
    """The document version a design-review task completes against."""

    board_copy: QualifiedProjectId
    key: VersionKey


@dataclass(frozen=True)
class Item:
    """The fields of the engine's `HumanItem` this host reads, validated where they arrive."""

    id: ItemId
    kind: Kind
    state: State
    priority: Priority
    title: str
    body: str
    approval: Approval | None
    #: Why it was dismissed, when it was dismissed with a reason.
    reason: str | None
    #: The answers a question offers; none on a task, and none on a question offering none.
    choices: tuple[Choice, ...] = ()
    #: The choice an answered question was answered with, when it names one.
    choice: ChoiceId | None = None
    #: An answer's own words, or a completed task's comment, when there are any.
    text: str | None = None

    @classmethod
    def parse(cls, record: object) -> Item:
        """``record`` as an item, or :class:`OSError` naming the field that is not one."""
        if not isinstance(record, dict):
            raise OSError(f"the engine answered {type(record).__name__} for an item")
        fields: Mapping[str, object] = record

        def text(name: str, held: Mapping[str, object] = fields) -> str:
            value = held.get(name)
            if not isinstance(value, str) or not value.strip():
                raise OSError(f"the engine answered an item whose {name} is {value!r}")
            return value

        kind, state, priority = text("kind"), text("state"), text("priority")
        if kind not in KINDS or state not in STATES or priority not in PRIORITIES:
            raise OSError(f"the engine answered an item of {kind}, {state}, {priority}")
        approval = fields.get("approval")
        if approval is not None and not isinstance(approval, dict):
            raise OSError(f"the engine answered an item whose approval is {approval!r}")
        resolution = fields.get("resolution")
        if resolution is not None and not isinstance(resolution, dict):
            raise OSError(f"the engine answered an item whose resolution is {resolution!r}")
        held: Mapping[str, object] = {} if resolution is None else resolution
        if not set(held) <= RESOLUTION_FIELDS:
            raise OSError(f"the engine answered an item whose resolution is {resolution!r}")
        reason, answer, choice = held.get("reason"), held.get("text"), held.get("choice")
        if reason is not None and not isinstance(reason, str):
            raise OSError(f"the engine answered an item whose reason is {reason!r}")
        if answer is not None and not isinstance(answer, str):
            raise OSError(f"the engine answered an item whose text is {answer!r}")
        provenance = held.get("provenance")
        if provenance is not None and provenance not in PROVENANCES:
            raise OSError(f"the engine answered an item whose provenance is {provenance!r}")
        choices = _choices(fields.get("choices"))
        if choices and kind != "question":
            raise OSError(
                f"the engine answered a {kind} offering choices, which only a question may"
            )
        if choice is not None and (
            not isinstance(choice, str)
            or state != "answered"
            or choice not in {offered.id for offered in choices}
        ):
            raise OSError(
                f"the engine answered a {state} item whose choice is {choice!r}, which only an "
                "answered question offering that choice may carry"
            )
        return cls(
            id=ItemId(text("id")),
            kind=kind,
            state=state,
            priority=priority,
            title=text("title"),
            body=text("body"),
            approval=None
            if approval is None
            else Approval(
                QualifiedProjectId(text("board_copy", approval)),
                VersionKey(text("key", approval)),
            ),
            reason=reason,
            choices=choices,
            choice=None if choice is None else ChoiceId(choice),
            text=answer,
        )


class Unavailable(Exception):  # noqa: N818 - the engine's own word for this refusal
    """Human items are off on this host, so the engine raised, read and changed nothing."""


def verb(arguments: Sequence[str]) -> str:
    """Run ``onepipeline human-items <arguments>`` and answer its standard output.

    :class:`Unavailable` when human items are off; :class:`OSError` naming the engine's own
    refusal for anything else it would not do.
    """
    done = subprocess.run(  # noqa: S603 - this checkout's own wrapper, with arguments as data
        [str(ENGINE), "human-items", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    if done.returncode == 0:
        return done.stdout
    if UNAVAILABLE in done.stderr:
        raise Unavailable
    refusal = done.stderr.strip() or f"exit status {done.returncode}"
    raise OSError(f"`onepipeline human-items {arguments[0]}` refused: {refusal}")


def _json(text: str) -> object:
    """``text`` as the JSON the engine answers, or :class:`OSError` saying it is not JSON."""
    try:
        return json.loads(text)
    except ValueError as exc:
        raise OSError(f"the engine answered something other than JSON: {exc}") from exc


def receipt_item(text: str) -> Item:
    """The item a mutation receipt ``{item: {item, blocked}, event_id}`` names."""
    receipt = _json(text)
    view = receipt.get("item") if isinstance(receipt, dict) else None
    if not isinstance(view, dict) or "item" not in view:
        raise OSError("the engine's receipt names no item")
    return Item.parse(view["item"])


def open_items(*, plan: str | None = None, kind: Kind | None = None) -> list[Item]:
    """Every open item the engine lists, narrowed by its own ``--plan`` and ``--kind`` selectors.

    Each entry is the engine's `{item, blocked}` view; one that is not is refused rather than
    dropped, because a list missing an item reads exactly like a queue without it.
    """
    arguments = ["list", "--json"]
    if plan is not None:
        arguments += ["--plan", plan]
    if kind is not None:
        arguments += ["--kind", kind]
    listed = _json(verb(arguments))
    if not isinstance(listed, list):
        raise OSError("the engine's item list is not a list")
    items = []
    for view in listed:
        if not isinstance(view, dict) or "item" not in view:
            raise OSError(f"the engine's item list holds {view!r}, which is no item view")
        items.append(Item.parse(view["item"]))
    return items


def raise_phase(phase: str, context: Mapping[str, object]) -> Item | None:
    """Raise what the hook configured for ``phase`` expands to against ``context``.

    The context is handed to the engine as a file, as data: it names the owner, the dedup
    key, template variables and what the hook aggregates, and never a command. ``None`` when
    the expansion raises nothing — a ticket review over no tickets, which retires the one an
    earlier occurrence raised.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as staged:
        json.dump(context, staged)
        staged.flush()
        answered = verb(["raise", "--phase", phase, "--context", staged.name])
    return None if answered.strip() == "null" else receipt_item(answered)


def complete(item: ItemId) -> Item:
    """Complete the open task ``item``, through its approval authority where it names one."""
    return receipt_item(verb(["complete", item]))


def dismiss(item: ItemId, reason: str) -> Item:
    """Dismiss the open item ``item``, recording ``reason``."""
    return receipt_item(verb(["dismiss", item, "--reason", reason]))


def acting_session() -> str | None:
    """The session this process acts as, as `scripts/launcher-session.sh` answers it.

    Asked of that script rather than restated, because it is the one definition of which
    session a process on this host acts as — the variable a launch exported, else the
    harness session it runs in — and an item owned by a session no other verb would name is
    one no watch delivers. ``None`` when the process acts as no session; :class:`OSError`
    when the helper itself could not be read, rather than an answer it never gave.
    """
    done = subprocess.run(  # noqa: S603 - this checkout's own helper, sourced by a fixed program
        [
            "bash",
            "-c",
            '. "$1" >/dev/null || exit 3; printf %s "${ONEPIPELINE_LAUNCHER_SESSION:-}"',
            "acting-session",
            str(LAUNCHER_SESSION),
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
        env=os.environ.copy(),
    )
    if done.returncode != 0:
        raise OSError(
            f"{LAUNCHER_SESSION} could not say which session this process acts as: "
            f"{done.stderr.strip() or f'exit status {done.returncode}'}"
        )
    return done.stdout or None
