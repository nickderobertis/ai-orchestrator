"""Copy a cleared plan onto the board this repository plans against.

A plan of this repository is drafted in a local Markdown source, cleared there by `just
review-plan`, and only then copied onto the `plans` GitHub Projects board it is launched
from. That order is not a preference: a review record is one entry of the task's **own
Markdown document**, so :data:`~orchestrator.plan_store.WRITABLE_PLUGIN` is the only
plugin a record can be written into, and a plan authored on the board can therefore
never carry one. `just check-plan` refuses it for want of a record and `just review-plan`
has nowhere to put one — a dead end an author reaches by following the obvious route,
with no command in it telling them where they should have started.

So this command is the missing step, and its whole addition is that the ordering is
enforced by a command rather than remembered by an operator. It composes: it spends no
judged turn, reviews nothing itself, and changes neither of the two commands beside it.
What it does before writing anything to the destination is read the plan and refuse it
when any of its tasks carries no review record for that task's current authored
content, or when its project record carries no plan-level one for the plan as it stands
— the same two questions `just check-plan` asks, answered by calling
:func:`~orchestrator.plan_review.unreviewed` and
:func:`~orchestrator.plan_review.plan_unreviewed` rather than by restating how either
record is keyed. A second implementation of that key would be a second answer to one
question, and would disagree with the first the moment either moved. A copied plan
carries both: each record is an entry of the metadata map the store's own copy carries,
on the task and on the project.

**The plan's documents are copied beside it, and that is not an extra.** The store's
`project copy` carries the project and its tasks and no document at all, while what a
person approves a plan as is its **design document** — so a plan copied without it arrives
on the board with nothing to approve and can never be launched. The documents go over as a
second call to the store's own `document copy`, after the project landed, and the approval
travels with them the way a review record travels with a task: it is an ordinary entry of
the metadata map the copy carries. Approve where you draft, then copy up.

**Copying is a write, so its refusals are told apart by exit status.** :data:`UNREVIEWED`
is this command's own refusal, made before the store is asked to do anything;
:data:`COPY_REFUSED` is the destination refusing a plan every task of which carried a
record. A builder that could not tell those apart would read "nothing has reviewed this"
as an outage of the board, and retry it.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from collections.abc import Sequence

from onetaskgraph_sdk import CopyReport  # type: ignore[import-untyped]  # Package omits py.typed.

from orchestrator import plan_review, plan_store

#: The source a plan of this repository is copied into when the caller names none: the
#: `plans` GitHub Projects board `onetaskgraph.yaml` configures, which is where a plan of
#: this repository is stored and launched from. A literal rather than a value derived
#: from that file, for the reason `tests/test_plan_source_roots.py` holds the source's own
#: five values as literals — this board is never repointed, because a live run's
#: settlements are projected back to the project it was launched from.
BOARD = "plans"

#: The store verbs this composes with. Named here because both the invocation and the
#: refusal that reports its exit status have to agree about what was run.
COPY = ("project", "copy")
COPY_DOCUMENTS = ("document", "copy")

#: The one flag this command applies to both SDK copy calls: `--dry-run` says the whole
#: command writes nothing, so the document copy must receive it as well as the project copy.
DRY_RUN = "--dry-run"

#: Nothing was refused and the destination holds the plan.
OK = 0

#: **This** command's refusal: some task of the plan carries no review record for its
#: current authored content, and nothing was written to the destination.
UNREVIEWED = 1

#: The plan could not be read, the review bar could not be composed, or the store CLI is
#: not installed — so nothing was judged and nothing was written.
UNREADABLE = 2

#: The **destination's** refusal: every task carried a record, the copy was attempted,
#: and the store answered non-zero. Distinct from :data:`UNREVIEWED` so a caller can tell
#: "nothing has reviewed this" from "the board would not take it".
COPY_REFUSED = 3


def main(argv: Sequence[str] | None = None) -> int:
    """Copy a qualified plan project into a configured source, from `just copy-plan`.

    The remaining arguments are translated to the SDK's project-copy parameters. A `--set` in
    the pass-through configures the **copy** and not the pre-flight read, which the
    store makes through this checkout's own configuration and environment. So a source
    is repointed in `onetaskgraph.yaml` or through the store's `ONETASKGRAPH_`
    variables, both of which the whole command sees, rather than through a flag only
    half of it does.

    The review pre-flight is unconditional, `--dry-run` included. Exempting a trial run
    would mean this command deciding what the passed-through arguments mean, which is a
    second reading of the store's own flag grammar; and an operator who wants to see
    what a copy would do to an unreviewed plan has been told the one thing they can do
    about it either way.
    """
    parser = argparse.ArgumentParser(
        prog="copy-plan",
        description=(
            "Copy a plan project, and the tasks in it, into another configured source, "
            "refusing one whose criteria nothing has reviewed."
        ),
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    # This default is driven end to end only as far as the pre-flight refusal that names
    # it, and the reason is what it is: it resolves to the live GitHub Projects board, so
    # a journey that let the copy run would be its own rate-limit burst and would leave a
    # project behind on the store every other run of this repository reads. The store's
    # configuration layers cannot stand a local directory in for it either — the file
    # layer's `github-projects` settings survive an environment override of the plugin
    # and the schema then refuses them. What covers it instead is three overlapping
    # proofs: `tests/test_plan_source_roots.py` reconciles this value against the
    # configured source, `tests/test_plan_copy.py` asserts it is what `copy` is handed,
    # and the journey drives a copy that lands for real under an explicit `--to`.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    parser.add_argument(
        "--to",
        dest="destination",
        default=BOARD,
        metavar="SOURCE",
        help=f"the configured source to copy into (default: {BOARD})",
    )
    args, passthrough = parser.parse_known_args(argv)
    try:
        records = plan_store.read_tasks(args.project)
    except (OSError, ValueError) as exc:
        print(
            f"copy-plan: cannot read project {args.project}: {exc}; nothing was copied. "
            f"Pass the qualified project id you would hand `just check-plan`",
            file=sys.stderr,
        )
        return UNREADABLE
    # tests/test_plan_copy.py covers this and no journey can: the bar is composed from
    # this checkout's own tracked files, so a recipe run from here cannot remove one.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    try:
        pending = plan_review.unreviewed(records)
    except OSError as exc:
        print(
            f"copy-plan: cannot fingerprint the review bar this plan would be cleared "
            f"against: {exc}; nothing was copied. Run `just bootstrap` from the "
            f"repository root and retry",
            file=sys.stderr,
        )
        return UNREADABLE
    if pending:
        named = ", ".join(task.node_id for task in pending)
        print(
            f"copy-plan: {len(pending)} task(s) carry no review record for their current "
            f"authored content: {named}. Nothing was copied into {args.destination!r}. A "
            f"plan is cleared where it is drafted, because a review record is an entry of "
            f"the task's own Markdown document and a board is not a directory — so review "
            f"it with `just review-plan {args.project}` while it is still local, then copy "
            f"it up.",
            file=sys.stderr,
        )
        return UNREVIEWED
    # The plan whole, read by the same two store calls `just review-plan` reads it by, so
    # the key this compares against is the one that command wrote. Refused before the
    # store is asked to write anything, for the reason the per-task refusal is: a board
    # is not a directory, and a plan copied without this record can never earn one there.
    try:
        plan = plan_store.read_plan(args.project, records)
        pending_plan = plan_review.plan_unreviewed(plan_store.project_record(args.project), plan)
    except (OSError, ValueError) as exc:
        print(
            f"copy-plan: cannot read the plan-level review record of {args.project}: {exc}; "
            f"nothing was copied. Pass the qualified project id you would hand "
            f"`just check-plan`",
            file=sys.stderr,
        )
        return UNREADABLE
    if pending_plan:
        print(
            f"copy-plan: every task of {args.project} carries a review record, and its "
            f"project record carries no plan-level one for the plan as it stands — nothing "
            f"has read the plan whole for the adoption its goal needs. Nothing was copied "
            f"into {args.destination!r}. Review it with `just review-plan {args.project}` "
            f"while it is still local, then copy it up.",
            file=sys.stderr,
        )
        return UNREVIEWED
    return copy(args.project, args.destination, passthrough)


def copy(project: str, destination: str, passthrough: Sequence[str]) -> int:
    """Run the store's own copy verb, and answer what an operator should read it as.

    The verb's per-record report — one line naming each project and task and whether it
    was created, updated or unchanged — is this command's product, so it is left to
    reach the caller's own streams rather than captured and summarized.
    """
    options = _copy_options(passthrough)
    try:
        report = plan_store.sdk(
            plan_store.client().project_copy(project, to=destination, **options)
        )
        _report(report)
    except Exception as exc:
        print(f"copy-plan: {exc}", file=sys.stderr)
        print(
            f"copy-plan: onetaskgraph {' '.join(COPY)} refused copying {project} into "
            f"{destination!r}, and reported "
            f"why above. Every task of this plan carried a review record, so this is the "
            f"destination refusing the copy rather than the plan being unreviewed: check "
            f"that {destination!r} is a source `just plans sources list` names and that "
            f"its credential and repository reach it, then run this command again — a "
            f"copy that partly landed is resumed by repeating it",
            file=sys.stderr,
        )
        return COPY_REFUSED
    return _documents(project, destination, passthrough)


def _documents(project: str, destination: str, passthrough: Sequence[str]) -> int:
    """Copy ``project``'s documents after its project record landed, and report the same way.

    Read through `orchestrator/plan_store.py` rather than by asking the store for ids in a
    second shape, and skipped outright when the plan holds none: `document copy` requires
    at least one id, so a plan with no document would otherwise refuse the whole command
    for having nothing to do.

    A document that a *previous* copy already put on the destination carries that copy's
    recorded origin, so the store updates it rather than duplicating it and nothing has to
    say how the two correspond.
    """
    try:
        documents = plan_store.read_documents(project)
    # tests/test_plan_copy.py drives this: reaching it through the recipe means breaking
    # the store between the pre-flight read above and this one.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    except OSError as exc:
        print(
            f"copy-plan: the plan landed in {destination!r}, but its documents could not be "
            f"read out of {project}: {exc}. The design document a person approves this plan "
            f"as may not be there; run this command again once the store answers",
            file=sys.stderr,
        )
        return COPY_REFUSED
    if not documents:
        return OK
    try:
        report = plan_store.sdk(
            plan_store.client().document_copy(
                [str(document.qualified_id) for document in documents],
                to=destination,
                dry_run=DRY_RUN in passthrough,
            )
        )
        _report(report)
    except Exception as exc:
        print(f"copy-plan: {exc}", file=sys.stderr)
        print(
            f"copy-plan: the plan landed in {destination!r}, but onetaskgraph "
            f"{' '.join(COPY_DOCUMENTS)} refused carrying its "
            f"{len(documents)} document(s) over, and reported why above. Until they land "
            f"there is nothing on {destination!r} for a person to approve this plan as, so "
            f"run this command again — a copy that partly landed is resumed by repeating it",
            file=sys.stderr,
        )
        return COPY_REFUSED
    return OK


def _copy_options(arguments: Sequence[str]) -> dict[str, object]:
    """Translate the SDK project-copy parameters exposed by this recipe."""
    translated = {
        "default_sources",
        "dry_run",
        "match_by",
        "member",
        "no_tasks",
        "page_size",
        "recreate",
        "set",
    }
    sdk_parameters = set(inspect.signature(plan_store.client().project_copy).parameters) - {
        "id",
        "to",
    }
    if sdk_parameters != translated:
        raise OSError(
            "the onetaskgraph SDK project-copy parameters changed; update this recipe's "
            f"argument translation ({sorted(sdk_parameters)!r})"
        )
    options: dict[str, object] = {}
    index = 0
    while index < len(arguments):
        flag = arguments[index]
        match flag:
            case "--dry-run" | "--recreate" | "--no-tasks":
                options[flag.removeprefix("--").replace("-", "_")] = True
                index += 1
            case "--match-by" | "--member" | "--set" | "--page-size" | "--default-sources" if (
                index + 1 < len(arguments)
            ):
                key = flag.removeprefix("--").replace("-", "_")
                value = arguments[index + 1]
                if key in {"member", "set"}:
                    held = options.setdefault(key, [])
                    assert isinstance(held, list)
                    held.append(value)
                elif key == "page_size":
                    try:
                        options[key] = int(value)
                    except ValueError as exc:
                        raise OSError(f"--page-size requires an integer, got {value!r}") from exc
                elif key == "default_sources":
                    options[key] = value.split(",")
                else:
                    options[key] = value
                index += 2
            case _:
                raise OSError(f"unsupported project-copy argument {flag!r}")
    return options


def _report(report: CopyReport) -> None:
    """Render the SDK copy report one record per line."""
    for item in report.items:
        print(item.model_dump_json(exclude_none=True))
    rewritten = report.references_rewritten or 0
    unresolved = report.references_unresolved or 0
    ambiguous = report.references_ambiguous or 0
    if rewritten or unresolved or ambiguous:
        print(f"references: {rewritten} rewritten, {unresolved} unresolved ({ambiguous} ambiguous)")
