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
content — the same question `just check-plan` asks, answered by calling
:func:`~orchestrator.plan_review.unreviewed` rather than by restating how a record is
keyed. A second implementation of that key would be a second answer to one question, and
would disagree with the first the moment either moved.

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
import subprocess
import sys
from collections.abc import Sequence

from orchestrator import plan_review, plan_store
from orchestrator.root import REPO_ROOT

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

#: The one flag of the pass-through this command has an opinion about, and the reason it
#: has one: `--dry-run` says the whole command writes nothing, so a document copy that
#: ignored it would write while the command it belongs to was reporting that it had not.
#: Every other flag is the project copy's own and is not restated, guessed at, or split.
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

    Everything this parser does not recognise reaches the store's own copy verb
    untouched — `--dry-run`, `--recreate`, `--match-by <KEY>` — rather than being
    re-declared here, where a flag the store gained would be refused by a wrapper that
    had no opinion about it. One of those is worth knowing the reach of: a `--set` in
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
    return copy(args.project, args.destination, passthrough)


def copy(project: str, destination: str, passthrough: Sequence[str]) -> int:
    """Run the store's own copy verb, and answer what an operator should read it as.

    The verb's per-record report — one line naming each project and task and whether it
    was created, updated or unchanged — is this command's product, so it is left to
    reach the caller's own streams rather than captured and summarized.
    """
    try:
        binary = plan_store.store_binary()
    # The recipe runs `scripts/onetaskgraph-install.sh` before this, which heals a
    # checkout carrying no CLI, so a journey reaching this refusal would have to
    # uninstall the binary the rest of the suite runs against. `tests/test_plan_copy.py`
    # drives it against a substituted resolver instead.
    # llmlint: ignore[changed_behavior_has_e2e] see the note above this line
    except OSError as exc:
        print(
            f"copy-plan: {exc}; nothing was copied. Run `just bootstrap` from the "
            f"repository root, which installs the release this checkout pins",
            file=sys.stderr,
        )
        return UNREADABLE
    completed = subprocess.run(
        [binary, *COPY, project, "--to", destination, *passthrough],
        cwd=REPO_ROOT,
        env=plan_store.store_environment(),
        check=False,
    )
    if completed.returncode != 0:
        print(
            f"copy-plan: {plan_store.STORE} {' '.join(COPY)} exited "
            f"{completed.returncode} copying {project} into {destination!r}, and reported "
            f"why above. Every task of this plan carried a review record, so this is the "
            f"destination refusing the copy rather than the plan being unreviewed: check "
            f"that {destination!r} is a source `just plans sources list` names and that "
            f"its credential and repository reach it, then run this command again — a "
            f"copy that partly landed is resumed by repeating it",
            file=sys.stderr,
        )
        return COPY_REFUSED
    return _documents(binary, project, destination, passthrough)


def _documents(binary: str, project: str, destination: str, passthrough: Sequence[str]) -> int:
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
    dry_run = [DRY_RUN] if DRY_RUN in passthrough else []
    completed = subprocess.run(
        [
            binary,
            *COPY_DOCUMENTS,
            *(str(document.qualified_id) for document in documents),
            "--to",
            destination,
            *dry_run,
        ],
        cwd=REPO_ROOT,
        env=plan_store.store_environment(),
        check=False,
    )
    if completed.returncode != 0:
        print(
            f"copy-plan: the plan landed in {destination!r}, but {plan_store.STORE} "
            f"{' '.join(COPY_DOCUMENTS)} exited {completed.returncode} carrying its "
            f"{len(documents)} document(s) over, and reported why above. Until they land "
            f"there is nothing on {destination!r} for a person to approve this plan as, so "
            f"run this command again — a copy that partly landed is resumed by repeating it",
            file=sys.stderr,
        )
        return COPY_REFUSED
    return OK
