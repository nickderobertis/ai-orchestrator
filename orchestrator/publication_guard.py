"""Refuse a lifecycle node this host's own publication policy would refuse afterwards.

Two shapes reach a dispatch, run for as long as the work takes, and are refused only
once the branch is finished:

* a node whose ``title`` the destination repository's own ``commit-msg`` hook rejects.
  That title becomes the publication subject — ``onevcs`` passes it to the squash commit
  and to ``gh pr create``, and it is never re-derived — so a hook that refuses it refuses
  the publication. One node of this host lost finished, judge-passed work to a
  ``refactor:`` subject and its dependent was skipped for it;
* a node carrying a non-empty ``consumes`` on an identity whose workflow opens no change
  request. Consuming a release target is a wait on a publication that identity never
  makes, so the node is unpublishable by construction whatever its adoption mode says.
  Another node here ran an hour and thirty-six minutes before that was reported.

Both are knowable before anything is dispatched, from what this host already holds, and
this is where they are asked. The engine makes the same two refusals at its own loader;
what keeps the two agreeing is that **both read the destination repository's own hook**
rather than a copy of that repository's release-type policy — this module runs it, so
there is nothing here to keep in step with the rule.

A third shape is refused for what it does to the *record* rather than to the branch: a
node naming a GitHub-hosted repository on the reserved ``onepipeline.repo`` key. The
engine's contract puts a node's repository in the task record's own top-level
``repositories`` list as one normalized origin, and keeps that key for an identity such a
list cannot hold — a local checkout `onevcs` knows by its absolute path. A record naming
a hosted identity by its alias instead reaches the plan store with ``repositories``
empty, so the board files the task's issue in the orchestrator's own repository rather
than the one the work changes, where nobody reading that repository finds it. Which of
the two an identity is comes from `onevcs resolve`'s ``origin`` — a normalized origin or
a path — and is asked of the same registry the dispatch will resolve it through.

**What is refused is narrower than what is checked, deliberately.** A node whose
destination this host cannot resolve, one whose repository declares no ``commit-msg``
hook, and one whose workflow is a word this module does not know are each passed over
rather than refused: a plan is checked against repositories this checkout may never have
seen, and refusing one for what this host cannot see would refuse plans that launch
correctly today. Written to miss rather than to refuse a sound node, which is the trade
:mod:`orchestrator.criteria_guard` states and the reason it states.

Nothing here decides a plan's *structure*: the engine's own loader has already ruled on
that, and every field is read leniently for the same reason.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple, NewType

from orchestrator.plan_store import NodeId
from orchestrator.project_store import hosted_origin

#: The version-control CLI that owns the registry, the rules file, and every answer this
#: module asks of them. Resolved on the path rather than named absolutely, exactly as
#: `orchestrator/criteria_guard.py` resolves the engine.
ONEVCS = "onevcs"

#: The one `kind` a plan states, whose nodes carry no execution fields at all.
HUMAN = "human"

#: The metadata key the engine reserves for a repository `repositories` cannot hold. A
#: record naming one both ways is the engine's own refusal; what this module refuses is
#: the key carrying a value that list *could* hold. The name is held to the installed
#: engine by `tests/plan_tooling/test_check_plan_recipe_e2e.py`, whose structural journey
#: writes this key beside `repositories` and reads the loader's own refusal of the pair.
RESERVED_REPO_KEY = "onepipeline.repo"

#: A repository identity, which is what a policy belongs to: an alias is one of several
#: spellings of it, and a checkout path is a directory that happens to hold one.
RepoIdentity = NewType("RepoIdentity", str)


class Workflow(StrEnum):
    """The publication policy an identity's rules resolve, in `onevcs`'s own vocabulary.

    A closed set of four, which is what makes a value outside it answerable as *unknown*
    rather than as either half of the question below: a release that added a fifth would
    have this module refuse nothing about it rather than guess.
    `tests/test_publication_guard.py` reconciles these four against the installed
    `onevcs` rather than against this list, so a vocabulary that moved brings the enum
    due instead of leaving it describing a release nobody runs.
    """

    LOCAL_DIRECT = "local-direct"
    CHANGE_OPEN = "change-open"
    CHANGE_AUTO = "change-auto"
    CHANGE_DIRECT = "change-direct"

    @property
    def opens_a_change_request(self) -> bool:
        """Whether publishing under this policy opens a change request at all.

        `local-direct` builds the base's squash commit itself and opens none, so a
        release target consumed under it is a wait on a publication that never happens.
        """
        return self is not Workflow.LOCAL_DIRECT

    @classmethod
    def known(cls, named: str) -> Workflow | None:
        """``named`` as a policy, or ``None`` for a word this module does not know."""
        try:
            return cls(named)
        except ValueError:
            return None


#: How `onevcs rules check` states the policy an identity resolves to. Parsed from its
#: line-oriented report because that verb answers no JSON: the value is taken from
#: `rules check` and never from `onevcs resolve`'s `workflow`, which is `register`'s
#: derivation from the origin and is explicitly not the routing.
_PUBLICATION = re.compile(r"^publication:\s*(?P<workflow>\S+)", re.MULTILINE)

#: How long either verb, or a destination's own hook, is given to answer. A bound rather
#: than a wait: this runs in front of a launch an operator is holding, and a repository
#: whose hook does not return is one this module has nothing to say about.
TIMEOUT_SECONDS = 30


class Refusal(NamedTuple):
    """One thing this host's publication policy would refuse after the dispatch."""

    node: NodeId
    #: The field to correct, which is what `onepipeline plan check` renders a refusal
    #: against — so it names a field of the plan record rather than one of this module.
    field: str
    reason: str


class PublicationError(ValueError):
    """A plan node this host's publication policy would refuse after the dispatch."""


class Resolved(NamedTuple):
    """One repository as `onevcs resolve` answers for it.

    The three fields this module reads out of that verb's JSON, narrowed where they are
    read. Held to the installed `onevcs` rather than to this declaration: the check-plan
    journeys in `tests/plan_tooling/test_check_plan_recipe_e2e.py` and the reads-checkouts
    tier in `tests/test_publication_guard.py` ask the real verb about real scratch
    identities and assert on what comes back through these fields.
    """

    #: The repository identity, which is what a refusal names.
    identity: RepoIdentity
    #: The publication checkout, which is where the destination's own hook is read from.
    checkout: Path
    #: The origin the identity was registered from: a normalized `host/owner/name` for a
    #: hosted repository, an absolute path for a local one. ``None`` when the answer
    #: carried none, which nothing here reads as either.
    origin: str | None


class Destination(NamedTuple):
    """Where one node's work lands, as this host resolves it."""

    #: The repository identity, which is what a refusal names.
    identity: RepoIdentity
    #: The publication checkout, which is where the destination's own hook is read from.
    checkout: Path
    #: The publication policy the rules file resolves for that identity, or ``None``
    #: when it answered something outside the published vocabulary.
    workflow: Workflow | None
    #: The identity's origin, as :class:`Resolved` carries it.
    origin: str | None


class Node(NamedTuple):
    """One node of a plan that publishes, read at the plan's boundary."""

    id: NodeId
    title: str
    repo: str
    #: Whether it names any release target to consume. The targets themselves are only
    #: rendered into the refusal, so the mapping is kept as it was written.
    consumes: Mapping[str, Any]
    #: What the record wrote on the reserved ``onepipeline.repo`` key, or ``None`` when
    #: its repository came from ``repositories``. The loaded plan carries the store's
    #: metadata map verbatim beside the resolved ``repo``, which is the only way to tell
    #: the two spellings apart once the engine has read both to one field.
    reserved: str | None


def publishing_nodes(plan: object) -> Iterator[Node]:
    """Every node of ``plan`` whose work lands in a repository, in the order stated.

    Top-level tasks only, and that is the shape rather than a simplification: a
    lifecycle node running several steps on one branch names its repository and its
    title once, on the node, and the steps beneath it are dispatches on that same branch
    — so the publication those steps end in is the parent's.
    """
    match plan:
        case {"tasks": [*tasks]}:
            pass
        case _:
            return
    for task in tasks:
        match task:
            # A guard rather than a value pattern: a bare name in a pattern captures,
            # which would match every node and publish nothing. The only `kind` a plan
            # states is `human`, whose node carries no execution field at all; every
            # other shape falling through below is a node naming no repository or no
            # title, and so no publication subject to rule on.
            case {"kind": kind} if kind == HUMAN:
                continue
            case {"id": str(node_id), "title": str(title), "repo": str(repo)} if (
                node_id and title and repo
            ):
                consumes = task.get("consumes")
                metadata = task.get("metadata")
                reserved = (
                    metadata.get(RESERVED_REPO_KEY) if isinstance(metadata, Mapping) else None
                )
                yield Node(
                    NodeId(node_id),
                    title,
                    repo,
                    consumes if isinstance(consumes, Mapping) else {},
                    reserved if isinstance(reserved, str) and reserved else None,
                )
            case _:
                continue


def _asked(arguments: list[str]) -> str | None:
    """What ``onevcs`` answered, or ``None`` when it could not answer at all.

    Every caller treats ``None`` as "this host cannot say", which is the whole of why
    the failure modes are collapsed: an unregistered repository, a CLI that is not
    installed, and one that did not return are the same answer to a plan's author, who
    is being told nothing rather than being refused.
    """
    found = shutil.which(ONEVCS)
    if found is None:
        return None
    try:
        asked = subprocess.run(
            [found, *arguments],
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return asked.stdout if asked.returncode == 0 else None


def resolve(repo: str) -> Resolved | None:
    """What `onevcs resolve` answers for ``repo``, or ``None`` when it cannot answer.

    ``resolve`` says which identity a node's ``repo`` names, which checkout a landing
    fast-forwards, and which origin the identity was registered from, as JSON. The
    origin is read leniently — absent is ``None`` rather than a refusal of the whole
    answer — because the two refusals that need only the checkout are worth making on an
    answer that carries nothing else.
    """
    resolved = _asked(["resolve", repo])
    if resolved is None:
        return None
    try:
        # llmlint: ignore[boundary_inputs_validated] `onevcs resolve`'s own JSON answer;
        # every field taken out of it is narrowed on the lines below.
        answer = json.loads(resolved)
    except ValueError:
        return None
    match answer:
        case {"identity": str(identity), "publication_checkout": str(checkout)} if (
            identity and checkout
        ):
            pass
        case _:
            return None
    origin = answer.get("origin")
    return Resolved(
        RepoIdentity(identity),
        Path(checkout),
        origin if isinstance(origin, str) and origin else None,
    )


def record_repository(repo: str) -> str:
    """What a node's ``repo`` carries so its record names the repository as the contract keeps it.

    The normalized origin whenever there is one to name — spelled that way already, or
    resolved to one by `onevcs` from an alias, a clone URL, or a checkout path — because
    that is the value the record's own ``repositories`` list holds and the engine reads
    a node's repository from. Anything `onevcs` resolves to a path origin, and anything
    it cannot resolve at all, is answered as written: the first is the one case the
    reserved ``onepipeline.repo`` key exists for, and the second is not this host's to
    rewrite. `scripts/plan-brief.sh` asks this of every ``--repo`` a planning flow is
    given, which is how that flow's own records come to carry the field.
    """
    hosted = hosted_origin(repo)
    if hosted is not None:
        return hosted
    resolved = resolve(repo)
    # llmlint: ignore-block[boundary_inputs_validated] The value passed through is the
    # operator's own `--repo` as typed, and the launch is what rules on it: a repository
    # the registry cannot resolve is refused by name when the plan loads, before anything
    # is dispatched. Rewriting or refusing it here would hide what was typed from the
    # refusal that names it.
    if resolved is None or resolved.origin is None:
        return repo
    return hosted_origin(resolved.origin) or repo
    # llmlint: ignore-end[boundary_inputs_validated]


def destination(repo: str) -> Destination | None:
    """Where ``repo``'s work lands, or ``None`` when this host cannot resolve it.

    Two answers from two verbs, because each is authoritative for one of them.
    ``resolve`` says which identity a node's ``repo`` names and which checkout a landing
    fast-forwards, as JSON. The **policy** comes from ``rules check`` and never from
    ``resolve``'s own ``workflow`` field, which is what ``onevcs register`` derived from
    the origin and the checkout: `AGENTS.md` records that those columns are not the
    routing, and reading one for the other here would answer `local-direct` for every
    identity on this host as `remote`.
    """
    resolved = resolve(repo)
    if resolved is None:
        return None
    reported = _asked(["rules", "check", repo])
    stated = None if reported is None else _PUBLICATION.search(reported)
    return Destination(
        resolved.identity,
        resolved.checkout,
        None if stated is None else Workflow.known(stated["workflow"]),
        resolved.origin,
    )


def commit_msg_hook(checkout: Path) -> Path | None:
    """The ``commit-msg`` hook ``checkout`` declares, or ``None`` when it declares none.

    Asked of git rather than composed from ``core.hooksPath``: ``git rev-parse
    --git-path hooks`` is what honours that setting, an unset one, and a worktree whose
    ``.git`` is a file — and it is the same resolution git itself performs when it runs
    the hook, which is the point. A publication's disposable clone is given the lender's
    hooks path when it is cut, so the hook read here is the hook that will run.

    ``None`` for a repository that declares no hook, one whose hook is not an executable
    regular file, and one this host cannot ask git about at all. All three are the same
    thing to a plan's author: nothing about this title is knowable here.
    """
    try:
        asked = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--git-path", "hooks"],
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if asked.returncode != 0 or not asked.stdout.strip():
        return None
    hook = Path(os.path.join(checkout, asked.stdout.strip(), "commit-msg"))
    return hook if hook.is_file() and os.access(hook, os.X_OK) else None


def hook_refusal(hook: Path, checkout: Path, subject: str) -> str | None:
    """What ``hook`` reported about ``subject``, or ``None`` when it accepted it.

    The hook is **run** rather than read, which is what keeps this refusal and the
    repository's own rule from being two statements of one policy: the rule stays where
    its repository states it, and a repository that changes it changes what this refuses
    with nothing here to follow.

    A hook that cannot be run at all answers ``None`` — nothing was learned, so nothing
    is refused. A hook that runs and exits non-zero is a refusal whatever its reason,
    including one somebody has broken: a broken hook refuses the publication commit too,
    so reporting it before the dispatch is the accurate answer rather than a false one.
    """
    handle, path = tempfile.mkstemp(prefix="publication-subject-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as message:
            message.write(f"{subject}\n")
        try:
            ran = subprocess.run(
                [str(hook), path],
                cwd=str(checkout),
                text=True,
                capture_output=True,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    finally:
        os.unlink(path)
    if ran.returncode == 0:
        return None
    reported = (ran.stderr or ran.stdout).strip()
    return reported or f"it exited {ran.returncode} without saying why"


#: How much of another program's report a refusal carries. Bounded because the report is
#: a foreign repository's hook talking, and an unbounded one buries the node and the
#: field this refusal is about under however much that hook decided to say.
REPORT_LIMIT = 400

#: What a control character is replaced with. Replaced rather than dropped, so a report
#: that was nothing but control characters still reads as text that was there.
CONTROL_STAND_IN = "?"


def _condensed(reported: str) -> str:
    """A hook's own report as the one line a refusal can carry it on.

    Sanitized as well as collapsed, because this is another repository's program writing
    into a message this host prints to a terminal: a report carrying escape sequences
    could move the cursor, colour unrelated output, or overwrite the refusal above it,
    and one carrying no newline at all could be arbitrarily long. Whitespace is collapsed
    first so a newline is a space rather than a `?`, then every remaining control
    character is replaced, and the result is bounded with the truncation said rather than
    silent.
    """
    collapsed = " ".join(reported.split())
    printable = "".join(one if one.isprintable() else CONTROL_STAND_IN for one in collapsed)
    if len(printable) <= REPORT_LIMIT:
        return printable
    return f"{printable[:REPORT_LIMIT]}… [{REPORT_LIMIT} of {len(printable)} characters]"


def _title_refusal(node: Node, where: Destination) -> Refusal | None:
    """What the destination's own hook says about this node's publication subject."""
    hook = commit_msg_hook(where.checkout)
    if hook is None:
        return None
    reported = hook_refusal(hook, where.checkout, node.title)
    if reported is None:
        return None
    return Refusal(
        node=node.id,
        field="title",
        reason=(
            f"this node's title becomes the subject {where.identity} is published under, "
            f"and that repository's own commit-msg hook refuses it: {node.title!r} — "
            f"{_condensed(reported)}. The subject is derived once, before any of the work "
            f"exists, and is never re-derived — so the whole dispatch is paid for and the "
            f"branch is refused at publication. Retitle the node."
        ),
    )


def _consumes_refusal(node: Node, where: Destination) -> Refusal | None:
    """Whether this node awaits a release its own identity's workflow never publishes."""
    if not node.consumes or where.workflow is None or where.workflow.opens_a_change_request:
        return None
    named = ", ".join(sorted(str(dependency) for dependency in node.consumes))
    opening = ", ".join(one.value for one in Workflow if one.opens_a_change_request)
    return Refusal(
        node=node.id,
        field="consumes",
        reason=(
            f"this node consumes a release target ({named}) while {where.identity} "
            f"resolves the {where.workflow.value!r} workflow, which lands on the base "
            f"itself and opens no change request — so the release it would wait on is one "
            f"this identity never publishes, and the node is unpublishable whatever its "
            f"adoption mode says. Drop `consumes`, or publish this node's work through "
            f"an identity whose workflow opens a change request ({opening})."
        ),
    )


def _reserved_key_refusal(node: Node, where: Destination) -> Refusal | None:
    """Whether this node names a hosted repository on the key reserved for a local one."""
    if node.reserved is None or where.origin is None:
        return None
    origin = hosted_origin(where.origin)
    if origin is None:
        return None
    return Refusal(
        node=node.id,
        field="repo",
        reason=(
            f"this node names {node.reserved!r} on the reserved `{RESERVED_REPO_KEY}` key, "
            f"and `onevcs` resolves that identity to the normalized origin {origin!r} — a "
            f"value the task record's own top-level `repositories` list holds, and the one "
            f"the engine reads a node's repository from. A record that names it by alias "
            f"instead reaches the plan store with `repositories` empty, which files the "
            f"task's issue in the orchestrator's own repository rather than the one this "
            f"work changes. Write `repositories: [{json.dumps(origin)}]` and drop "
            f"`{RESERVED_REPO_KEY}`, which is reserved for an identity a normalized origin "
            f"cannot hold: a local checkout `onevcs` knows by its absolute path."
        ),
    )


def refusals(plan: object) -> list[Refusal]:
    """Everything this host's publication policy would refuse about ``plan``.

    Everything, rather than the first thing about each node: the three are independent
    fields with independent corrections, and a node told only about its title would be
    retitled, re-checked, and refused again for what it consumes. A plan's author reads
    one report and makes one pass.

    The destination is resolved once per repository a plan names rather than once per
    node: a plan states the same repository on most of its nodes, and asking ``onevcs``
    again for each of them would spend the same two answers over and over.
    """
    resolved: dict[str, Destination | None] = {}
    found: list[Refusal] = []
    for node in publishing_nodes(plan):
        if node.repo not in resolved:
            resolved[node.repo] = destination(node.repo)
        where = resolved[node.repo]
        if where is None:
            continue
        found.extend(
            refused
            for refused in (
                _reserved_key_refusal(node, where),
                _consumes_refusal(node, where),
                _title_refusal(node, where),
            )
            if refused is not None
        )
    return found


def check_plan(plan: object) -> None:
    """Raise :class:`PublicationError` for the first thing this host would refuse.

    The raising face of :func:`refusals`, for the path that reports one refusal at a
    time; `orchestrator/plan_check.py` reports them all through the verb instead. Both
    read the same answer, so the two paths cannot disagree about a plan — what differs
    is how much of that one answer each is able to print.
    """
    for refused in refusals(plan):
        raise PublicationError(f"{refused.node}: {refused.reason}")
