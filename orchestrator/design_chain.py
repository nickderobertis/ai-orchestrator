"""Which repository's layer a plan's design document resolves through: the rule, once.

The design document is a rendering of the `design-doc` template, and the engine resolves
that name through layers — a repository's own `.onepipeline/templates/design-doc.md.j2`
over this host's `templates/design-doc.md.j2` — so a repository can override any block of
it. Which repository's layer answers is the caller's to say, and two callers have to say
the same thing for one plan: `scripts/finish-plan.sh`, whose writer renders the document,
and `orchestrator/design_approval.py`, whose approval key covers the chain digest the
document was rendered under. Each holding its own copy of the choice is how a document
would come to be rendered through one chain and approved against another, and every
approval of it read as stale from the day it was written. So the rule lives here, and both
read it.

**The rule.** When every task of the plan names exactly one repository, and it is the same
one, the resolve names `--repository <that origin>`, and the engine resolves that origin
to its registered checkout's layer. Otherwise — the tasks name several repositories, or
none, or the plan holds no task — the resolve names no repository, and the engine's own
rule stands: the working directory's layer.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Sequence

from orchestrator import plan_store

#: The name this host registers the design document's template under.
TEMPLATE_NAME = "design-doc"

#: The engine flag that names a repository origin whose registered checkout supplies the
#: repository layer.
REPOSITORY_FLAG = "--repository"


def chosen(repositories: Sequence[Sequence[str]]) -> str | None:
    """The one origin every task names, given each task's repositories, or ``None``.

    ``None`` unless there is at least one task and each names exactly one repository, the
    same one: a plan across several repositories has no one layer that speaks for it, and
    a plan naming none is one the working directory's layer answers for.
    """
    named = {tuple(one) for one in repositories}
    match sorted(named):
        case [(origin,)]:
            return origin
        case _:
            return None


def resolve_arguments(repository: str | None) -> list[str]:
    """The arguments `onepipeline template resolve design-doc` takes after its name."""
    return [REPOSITORY_FLAG, repository] if repository is not None else []


def resolve_command(repository: str | None) -> str:
    """The resolve command, as a person or a writer runs it, for ``repository``."""
    words = ["onepipeline", "template", "resolve", TEMPLATE_NAME]
    return shlex.join([*words, *resolve_arguments(repository), "--json"])


def plan_repository(project: str) -> str | None:
    """The origin the rule picks for the plan ``project``, read out of the plan store."""
    # The home's tasks and its members', as `plan_store.read_tasks` reads them: a plan the
    # store spread across sources keys its approval on the repositories of all of them, the
    # same chain wherever the home landed and the same one it had where it was drafted.
    repositories = [
        [repository.model_dump() for repository in held.item.repositories]
        for held in plan_store.plan_listing(project)
    ]
    return chosen(repositories)


def main(argv: Sequence[str] | None = None) -> int:
    """Print the resolve command the rule gives for one plan, for `scripts/finish-plan.sh`."""
    parser = argparse.ArgumentParser(
        prog="design-chain",
        description="Print the design-doc resolve command the rule gives for one plan.",
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    args = parser.parse_args(argv)
    try:
        repository = plan_repository(args.project)
    except OSError as exc:
        print(f"design-chain: {exc}", file=sys.stderr)
        return 1
    print(resolve_command(repository))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
