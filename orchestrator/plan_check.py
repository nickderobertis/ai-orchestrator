"""This repository's plan checks, as one executable the engine's own plan check runs.

`onepipeline plan check <source:project> --check <path>` runs the **engine's own plan
loader** — every refusal `onepipeline start` would make — and then hands each registered
check the loaded plan as one JSON document on stdin. This module is that check: the
criteria checks in :mod:`orchestrator.criteria_guard` and the review record in
:mod:`orchestrator.plan_review`, answered in the shape the verb reads.

**Why this repository stopped deciding a plan's structure for itself.** The check that
ran before this one re-implemented the loader's rules in Python; it drifted in both
directions, passing shapes the launch then refused and refusing sound ones. Making the
loader the thing that decides means a pre-dispatch refusal is a launch refusal by
construction, rather than by a second implementation somebody has to keep in step.
`AGENTS.md` carries what that drift cost.

What is left here is what no published CLI can know: the review bar each node resolves
to out of the `oneagentgraph` that `onepipeline` links, this host's own operational
appendix, and whether anything has reviewed the criteria at all.

**The contract, which is the whole of this module's interface.** The plan arrives on
stdin as `{"schema_version", "name", "goal", "concurrency", "tasks": [...]}`, each task
carrying the engine's resolved node fields and `"metadata"` — the store's own metadata
map verbatim, including the keys outside the `onepipeline.` namespace that this
repository's review record lives in. The answer goes to stdout as
`{"refusals": [{"node", "field", "reason"}, ...]}` with **exit 0 whether or not it
refused**: a non-zero exit means the check could not be run, and is reported as such
rather than as an accept. So a configuration this checkout cannot read — a missing
review bar, an absent appendix — exits non-zero deliberately, because "this repository
cannot say" is not the same answer as "this plan is fine".
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TypedDict

from orchestrator import criteria_guard, plan_review, plan_store
from orchestrator.criteria_guard import CriteriaError
from orchestrator.root import REPO_ROOT

#: What the verb sets in this process's environment, and the only thing a check may
#: read out of it about the protocol itself. Read rather than required: the document on
#: stdin is the interface, and refusing a plan because an environment marker was absent
#: would fail a check an operator ran by hand over a plan that is fine.
SCHEMA_ENV = "ONEPIPELINE_PLAN_CHECK_SCHEMA"

#: The qualified project the wrapper is checking, so a refusal about a missing review
#: record can name the command that records one. The document carries no project id —
#: it is the loaded *plan* — and `just review-plan <source>:<project>` is the whole of
#: what an operator does about that refusal, so a message without it names no action.
PROJECT_ENV = "ORCHESTRATOR_PLAN_CHECK_PROJECT"

#: What a refusal about a missing review record says when nothing named the project.
UNNAMED_PROJECT = "<source>:<project>"

#: The interpreter :data:`~orchestrator.criteria_guard.PLAN_CHECK_SCRIPT` runs this
#: module on. The wrapper sets it to its own, so a command that resolved this package
#: cannot then spawn a check that fails to import it.
PYTHON_ENV = "ORCHESTRATOR_PYTHON"

#: The environment name a journey establishes an engine under, and the only reason it
#: exists: :func:`main`'s direct path is reachable only against an engine carrying no
#: `plan check`, and this repository installs one that has it. It selects nothing else —
#: the roles a persona name resolves to are still read out of the installed binary, and
#: both paths run these same checks over the same plan — so it cannot make a plan pass
#: that the other path refuses.
ENGINE_ENV = "ORCHESTRATOR_PLAN_CHECK_ENGINE"


class Refusal(TypedDict):
    """One refusal, in the three fields the verb reads and renders."""

    #: The node the refusal is about, or ``None`` for one about the plan as a whole.
    node: str | None
    #: The field of that node the refusal is about, or ``None``.
    field: str | None
    reason: str


def _named_project(named: str | None) -> str:
    """``named`` when it is a qualified project id, and the placeholder otherwise.

    Validated rather than interpolated, and answered as the placeholder rather than
    refused: this value only ever reaches a refusal's own prose, telling an operator
    which project to run `just review-plan` against. Something that is not a project id
    would send them to a command that cannot work, while refusing the whole check over
    it would fail a plan for the wrapper's environment rather than for its criteria.
    """
    if named is None:
        return UNNAMED_PROJECT
    try:
        plan_store.qualified(named)
    except OSError:
        return UNNAMED_PROJECT
    return named


class Answer(TypedDict):
    """This check's whole answer on stdout."""

    refusals: list[Refusal]


def _reason(node_id: str, exc: CriteriaError) -> str:
    """``exc``'s message with the node id it opens with removed.

    The messages are written to stand alone on this repository's own stderr, so each
    names its node; the verb renders the node from the refusal's own field and would
    print it twice. Stripping here rather than rewording every message keeps one text
    for both paths, which is what stops the two answers drifting apart.
    """
    message = str(exc)
    prefix = f"{node_id}: "
    return message[len(prefix) :] if message.startswith(prefix) else message


def _node_refusals(document: object) -> Iterator[Refusal]:
    """Every refusal this repository's criteria checks make about ``document``.

    One refusal per node at most: the checks are ordered from the cheapest to state to
    the most specific, and a node whose persona cannot resolve has no bar to check its
    criteria against, so continuing would report a second refusal about the first one's
    cause.
    """
    for node in criteria_guard.dispatched_nodes(document):
        try:
            bar = criteria_guard.resolve_bar(node.persona)
        except CriteriaError as exc:
            yield Refusal(node=node.id, field="persona", reason=_reason(node.id, exc))
            continue
        try:
            criteria_guard.check(node.task, node.id, bar)
            criteria_guard.check_appendix(node.task, node.id)
        except CriteriaError as exc:
            yield Refusal(node=node.id, field="task", reason=_reason(node.id, exc))


# llmlint: ignore[suppressions_justified] The document is the engine's own open plan
# contract; every field this reads is narrowed at the read.
def _task_record(task: object) -> plan_store.StoreTask:
    """One document task as the record a review key is computed over.

    A :class:`~orchestrator.plan_store.StoreTask` rather than a shape of its own,
    because :func:`orchestrator.plan_review.review_key` is what decides which fields a
    review covers and a second carrier of those fields would be a second answer to
    that. ``qualified_id`` is the one field the loaded plan does not carry — it is the
    store's address for the record, and nothing on this path addresses one — so it is
    filled from the node id, which is what every message here names anyway.
    """
    read = task if isinstance(task, dict) else {}
    node_id = read.get("id")
    metadata = read.get("metadata")
    deps = read.get("deps")
    title = read.get("title")
    content = read.get("task")
    # An id that is not a string is not rendered into one: the engine's loader refuses a
    # node without one long before this runs, so a missing id here means the two readers
    # disagree — and a record addressed as `None` would refuse a node no plan contains.
    named = node_id if isinstance(node_id, str) else ""
    return plan_store.StoreTask(
        qualified_id=plan_store.QualifiedTaskId(named),
        node_id=plan_store.NodeId(named),
        title=title if isinstance(title, str) else "",
        content=content if isinstance(content, str) else None,
        metadata=metadata if isinstance(metadata, dict) else {},
        repositories=[],
        deps=tuple(
            plan_store.NodeId(dependency)
            for dependency in (deps if isinstance(deps, list) else [])
            if isinstance(dependency, str)
        ),
    )


def _review_refusals(document: object, project: str) -> Iterator[Refusal]:
    """One refusal per task of ``document`` that carries no review record.

    Per task rather than one naming them all, because the verb renders a refusal
    against the node it is about — and an operator reading a list of nodes inside one
    plan-wide refusal has to work out which of them it is talking about.
    """
    tasks = document.get("tasks") if isinstance(document, dict) else None
    records = [_task_record(task) for task in (tasks if isinstance(tasks, list) else [])]
    for task in plan_review.unreviewed(records):
        yield Refusal(
            node=task.node_id,
            field="metadata",
            reason=(
                f"no review record for its current authored content. Nothing has read "
                f"these criteria, which is how a plan written under time pressure reaches "
                f"a dispatch. Review it with `just review-plan {project}`, which records a "
                f"pass only for a plan held in a local Markdown store"
            ),
        )


def refusals(document: object, project: str) -> list[Refusal]:
    """Everything this repository refuses about ``document``, in the order it reads it.

    A malformed plan is reported as one refusal about the plan rather than raised: the
    engine's own loader has already refused every shape it knows, so what reaches here
    is either well-formed or something both readers disagree about, and a traceback
    would report that as a broken check rather than as a plan to fix.
    """
    found: list[Refusal] = []
    try:
        found.extend(_node_refusals(document))
    except CriteriaError as exc:
        found.append(Refusal(node=None, field=None, reason=str(exc)))
    found.extend(_review_refusals(document, project))
    return found


def dispatched(document: object) -> int:
    """How many dispatched nodes ``document`` states, or ``0`` when it states none."""
    try:
        return sum(1 for _ in criteria_guard.dispatched_nodes(document))
    except CriteriaError:
        return 0


def _runnable(named: str) -> bool:
    """Whether ``named`` is something this command could actually spawn.

    A bare name is left to the search path — that is what `shutil.which` answers — and a
    value carrying a separator is a path, which has to be an executable **regular file**:
    a directory carries the execute bit for traversal and would pass a permission check
    while being unspawnable. Validated before the probe rather than caught after it,
    because a spawn failure from an unrunnable value reads as an engine that refused the
    verb, and this command would then quietly take the narrower path over a plan the
    operator meant to have checked whole.
    """
    if os.sep in named:
        return Path(named).is_file() and os.access(named, os.X_OK)
    return bool(shutil.which(named))


def plan_check_engine() -> str | None:
    """The engine binary a plan check runs through, or ``None`` when there is none."""
    return os.environ.get(ENGINE_ENV) or shutil.which(criteria_guard.ENGINE)


def carries_plan_check(engine: str) -> bool:
    """Whether ``engine`` has a `plan check` verb to register a check with.

    Asked of the binary rather than of `config/onepipeline.version`, for the reason
    AGENTS.md gives about every version claim on this host: a pin describes what a
    release *would* link, and what answers a command is what was installed.
    """
    probe = subprocess.run(
        [engine, "plan", "check", "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return probe.returncode == 0


# llmlint: ignore[suppressions_justified] The verb's answer is another program's open
# JSON contract; every field is narrowed at the read.
# llmlint: ignore[changed_behavior_has_e2e] The `None` this answers for an entry no
# reader could act on is forward compatibility with a verb of some *other* shape, and
# this checkout installs exactly one engine which answers exactly one way — so a journey
# could only drive it by substituting the engine wholesale, which would be asserting on a
# fabricated answer rather than on anything the recipe meets.
# tests/test_plan_check.py drives every shape.
def rendered_refusal(refusal: object) -> str | None:
    """One refusal of the verb's answer as the line this command prints, or ``None``.

    ``None`` for an entry that carries no reason at all, so a verb that grew a field
    reports what it can rather than failing over its own answer.
    """
    if not isinstance(refusal, dict) or not isinstance(refusal.get("reason"), str):
        return None
    named = [
        part
        for part in (refusal.get("source"), refusal.get("node"), refusal.get("field"))
        if isinstance(part, str) and part
    ]
    return "check-plan: " + ": ".join([*named, refusal["reason"]])


# llmlint: ignore[suppressions_justified] The verb's answer is another program's open
# JSON contract; every field is narrowed at the read.
# llmlint: ignore[changed_behavior_has_e2e] Forward compatibility with a verb of some
# other shape, for the reason stated above `rendered_refusal`.
def rendered_unrunnable(entry: object) -> str | None:
    """One check the verb could not run, said the way an operator can act on it."""
    if not isinstance(entry, dict):
        return None
    # Narrowed at the read like every other field of this answer: a check that never
    # started carries no code at all, and anything that is not a whole number is not one
    # either. `bool` is excluded because it is an `int` in Python and `(exit True)` would
    # read as a code an operator could look up. An unusable value is dropped rather than
    # interpolated, so the sentence still names the check and what it reported.
    code = entry.get("exit_code")
    exited = isinstance(code, int) and not isinstance(code, bool)
    reported = entry.get("stderr")
    named = entry.get("check")
    return (
        f"check-plan: {named if isinstance(named, str) and named else 'a registered check'}"
        f" could not be run{f' (exit {code})' if exited else ''}: "
        f"{reported if isinstance(reported, str) and reported else 'it reported nothing'}"
    )


def _listed(answer: object, key: str) -> list[object]:
    """One list of the verb's answer, or an empty one when it carries none."""
    held = answer.get(key) if isinstance(answer, dict) else None
    return list(held) if isinstance(held, list) else []


def dispatched_in(project: str) -> criteria_guard.Counted:
    """How many nodes of ``project`` dispatch an agent, re-read from the store.

    **Read here rather than reported back by the registered check.** The only channel a
    spawned check has to the wrapper that registered it is a file, and a file whose path
    that wrapper hands over in the environment is a write any symlink on the way to it
    can redirect — so there is no side channel at all, and the count comes from the
    store this command can read for itself.

    Counting is not deciding, which is what keeps this from being the re-implementation
    the plug-in exists to retire: the engine's own loader has already ruled on this
    plan's structure and accepted it, and what this answers is how much of it this
    repository's checks were handed. It runs **after** that acceptance for the same
    reason — a reader of this repository's failing first would refuse a plan the launch
    would take, which is the drift, in the other direction.

    So a project this cannot re-read is answered as an unknown count rather than as a
    refusal: the plan was accepted, and a command that turned its own reader's limits
    into a verdict would be doing exactly what it stopped doing.
    """
    try:
        plan, _ = plan_store.read_project(project)
        return criteria_guard.Counted(sum(1 for _ in criteria_guard.dispatched_nodes(plan)))
    except (OSError, ValueError, CriteriaError) as exc:
        return criteria_guard.Counted(None, str(exc))


def check_through_engine(project: str, engine: str) -> int:
    """Check ``project`` through the engine's own loader and this repository's checks.

    Every refusal the verb returns is printed before anything of this command's own —
    engine refusals and registered-check refusals alike, each naming the source that
    made it, because they are two different loaders and an operator acting on one has
    to know which. The verb's exit code is this command's: 0 accepted, 1 at least one
    refusal, 2 a project that could not be read or a check that could not be run.
    """
    environment = dict(os.environ)
    environment[PROJECT_ENV] = project
    environment.setdefault(PYTHON_ENV, sys.executable)
    completed = subprocess.run(
        [
            engine,
            "plan",
            "check",
            project,
            "--check",
            # Relative, and the working directory below is what it resolves against:
            # the verb echoes a `--check` back as the source of every refusal that
            # check made, and an absolute path buries the node and the reason behind
            # the whole of this checkout's location.
            criteria_guard.PLAN_CHECK_SCRIPT.as_posix(),
            "--json",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        # llmlint: ignore[boundary_inputs_validated] Another program's open answer;
        # every field is narrowed by the readers above.
        answer = json.loads(completed.stdout)
    except ValueError:
        print(
            f"check-plan: `{criteria_guard.ENGINE} plan check` answered nothing this "
            f"command could read: {completed.stderr.strip() or 'it reported nothing'}",
            file=sys.stderr,
        )
        return 2
    for line in (rendered_refusal(one) for one in _listed(answer, "refusals")):
        if line is not None:
            print(line, file=sys.stderr)
    unrunnable = _listed(answer, "unrunnable")
    for line in (rendered_unrunnable(one) for one in unrunnable):
        if line is not None:
            print(line, file=sys.stderr)
    if completed.returncode != 0:
        if completed.stderr.strip():
            print(completed.stderr.strip(), file=sys.stderr)
        # Exit 2 with nothing the verb could name is the project itself: it was not
        # read, so nothing was judged, and what an operator does about that is pass the
        # id they were about to launch. Said here because the engine's own diagnostic is
        # about a store lookup and names no next command of this host.
        if completed.returncode == 2 and not unrunnable:
            print(
                f"check-plan: {project} could not be read, so nothing was judged; pass "
                f"the qualified project id you are about to hand `just orchestrate`",
                file=sys.stderr,
            )
        return completed.returncode if completed.returncode in {1, 2} else 2
    print(criteria_guard.accepted(dispatched_in(project), criteria_guard.THROUGH_ENGINE))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Check a qualified plan project before it is launched, from `just check-plan`.

    The engine's own plan loader decides a plan's structure wherever it can be asked to
    — every refusal `onepipeline start` would make, so that a pre-dispatch refusal is a
    launch refusal by construction rather than by a second implementation of its rules —
    and this repository's checks run beside it as a registered check. Against an engine
    with no such verb the same checks run directly, and which path was taken is reported
    either way: the two refuse different amounts, so a plan accepted by the narrower one
    is not the same claim.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Refuse a plan whose node would be judged against a demand its task does not state."
        )
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    args = parser.parse_args(argv)
    named = os.environ.get(ENGINE_ENV)
    if named and not _runnable(named):
        print(
            f"check-plan: {ENGINE_ENV} names {named!r}, which is not an executable this "
            f"command can run; unset it to use the `{criteria_guard.ENGINE}` this "
            f"checkout provisions",
            file=sys.stderr,
        )
        return 2
    engine = plan_check_engine()
    if engine is not None and carries_plan_check(engine):
        return check_through_engine(args.project, engine)
    return criteria_guard.check_directly(args.project)


def answer_on_stdin() -> int:
    """Read the loaded plan on stdin and answer the refusals it earns."""
    try:
        # llmlint: ignore[boundary_inputs_validated] The plan is the engine's own open
        # contract; every field read out of it is narrowed where it is read.
        document = json.load(sys.stdin)
    except ValueError as exc:
        print(
            f"plan-check: the plan on stdin is not valid JSON: {exc}. This check is "
            f"spawned by `onepipeline plan check`, which writes the loaded plan there; "
            f"run `just check-plan <source>:<project>` rather than this file, or pipe in "
            f"one plan document if you are driving it by hand",
            file=sys.stderr,
        )
        return 2
    project = _named_project(os.environ.get(PROJECT_ENV))
    try:
        answer = Answer(refusals=refusals(document, project))
    # tests/test_plan_check.py covers this, and no journey can: the review bar is
    # composed from this checkout's own tracked files, so a journey run from here
    # cannot remove one.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    except OSError as exc:
        print(
            f"plan-check: cannot read this checkout's own review configuration: {exc}; "
            f"run `just bootstrap` from the repository root and retry",
            file=sys.stderr,
        )
        return 2
    json.dump(answer, sys.stdout)
    sys.stdout.write("\n")
    return 0


# `python -m orchestrator.plan_check` is the *check* — what
# `scripts/plan-check.sh` execs and what the verb spawns. The wrapper above is the
# console script `orchestrator-check-plan`, which registers that script. Both ends of
# one contract live here so neither can be changed without the other in view.
if __name__ == "__main__":
    raise SystemExit(answer_on_stdin())
