"""`just plan` refuses a plan-authoring root it cannot write its project into.

The recipe resolves that root before it writes anything and writes its project under it,
so the two states below are the ones between a launch and a plan on disk: a root whose
`projects` is already something else, and one whose `tasks` is. Neither is reachable from
a root the recipe resolved for itself — that one is validated first, in the helper's own
words — so both are driven over a root this journey states, which is also how an operator
who has pointed the root somewhere reaches them.

They live in this project rather than beside the rest of `just plan`'s journeys because
of what they now spawn: the recipe resolves its root through the installed plan-store
CLI, so a copied checkout has to be one that CLI can be read in, and `plan-tooling` is the
project that owns this repository's host-tool plan journeys and is keyed on what they read.

Everything below the recipe is real — the real script, the real helpers it sources, the
real package the resolution imports, and the real store configuration — and nothing is
doubled: what is supplied is a root, which is an input.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import plan_root_variable
import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The brief these journeys launch from. Written here rather than read from `examples/`,
#: because this project's key covers no document under it: reading one would replay a
#: verdict recorded before it changed.
BRIEF = """## What
Decide whether the paginated listing's cursor is an opaque token or a node id.

Plan project: authoring:cursor-shape

## Why
The browser view cannot deep-link to a page until that is settled.

## Acceptance criteria
- The cursor's shape and its type are stated.
"""

#: Everything an enclosing dispatch would otherwise decide for these launches. The
#: plan-authoring root is the one that matters here — this suite runs inside a dispatch,
#: and a journey that kept an enclosing planning launch's root would refuse over a
#: directory it never stated.
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    plan_root_variable.name(),
)


def _environment(root: Path) -> dict[str, str]:
    """The environment one refused launch runs in, stating the root it is refused over.

    Smaller than the one the launching journeys beside this project build, and that is
    the point rather than an omission: both refusals below happen before the recipe
    reaches `onepipeline start`, so no run is created, no session is opened and no
    repository registry is touched — there is nothing here for a scratch identity to
    keep off this host's own.
    """
    return {
        **{key: value for key, value in os.environ.items() if key not in INHERITED},
        plan_root_variable.name(): str(root),
    }


def _detached_recipe(tmp_path: Path) -> Path:
    """A copy of the recipe's script and every seam it requires, outside this checkout.

    All of them, because the recipe refuses to launch a planner it could not establish an
    environment for before it writes anything, and a copy carrying only itself would be
    refused for the wrong reason. There are two such seams. The **ask** seam is the
    helper that establishes it and the wrapper that helper insists on. The
    **plan-authoring root** is the other: the helper that resolves it, the package that
    performs the resolution, and the store configuration that declares the source — the
    recipe writes its project under the root that resolves to, so a copy the store cannot
    be read in is a copy that cannot write a plan at all.

    The root the copy resolves is its own `.plans`, which is what makes these journeys
    about a checkout rather than about this one.
    """
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "plan.sh",
        # The grammar `scripts/plan.sh` reads its brief and its options through: a
        # checkout without it cannot get as far as the refusal these journeys are about.
        "plan-brief.sh",
        "credentials-env.sh",
        "ask-manager-env.sh",
        "ask-manager.sh",
        "plan-root-env.sh",
    ):
        copied = scripts / name
        copied.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        copied.chmod(0o755)
    # The package without its prose: what the resolution imports is code, and these
    # journeys belong to a tier whose key drops markdown, so copying a document would be
    # reading one.
    shutil.copytree(
        REPO_ROOT / "orchestrator",
        checkout / "orchestrator",
        ignore=shutil.ignore_patterns("*.md"),
    )
    shutil.copy(REPO_ROOT / "onetaskgraph.yaml", checkout)
    return scripts / "plan.sh"


def test_a_plan_directory_that_cannot_be_created_is_refused_before_the_launch(
    tmp_path: Path,
) -> None:
    """A record directory that cannot be made is said out loud rather than exited through.

    The recipe writes its project under the plan-authoring root it resolved, into that
    root's own `projects/` directory — so the state this guards is a root the launch may
    write into whose `projects` is already something else. That is what makes it a
    different failure from the root itself being unusable, which the helper refuses one
    step earlier and in its own words: this one is reached with the root validated, and
    without a guard it is `mkdir`'s message and an exit status, naming neither what was
    being attempted nor what to do.

    Driven by running the real script over a root of this journey's own, because a root
    the recipe resolves for itself is by construction one it can create that directory
    in.
    """
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    recipe = _detached_recipe(tmp_path)
    root = tmp_path / "plans"
    root.mkdir()
    (root / "projects").write_text("not a records directory\n", encoding="utf-8")
    working = tmp_path / "working"
    working.mkdir()

    refused = subprocess.run(  # noqa: S603 - the real script, over a root it cannot write into
        [str(recipe), str(brief)],
        cwd=working,
        env=_environment(root),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert "could not be created" in refused.stderr, refused.stderr
    assert str(root / "projects") in refused.stderr, refused.stderr


def test_a_plan_that_could_not_be_written_leaves_no_half_written_file_behind(
    tmp_path: Path,
) -> None:
    """A partial plan is removed, because the next launch would otherwise read it.

    A project is a directory of task records and one project record under one root, and
    the store writes the project record **last** so that a project becomes visible only
    with its tasks already beside it. So the partial state a failed write leaves is the
    task records without their project — at exactly the paths a rerun, or a manager
    relaunching with `just orchestrate`, would pick up — and the refusal has to take them
    with it.

    Induced by occupying the project record's own path, which is what makes the write
    fail at its last step with everything before it already on disk. The cause is stood
    in for because the real ones are not drivable — a device that fills mid-write, a
    process killed between two files — and it is the cause rather than the layer under
    test: what is measured is what the refusal leaves behind.

    Induced at the destination rather than at the interpreter, which is what this used to
    do: the recipe resolves its plan-authoring root through that same interpreter before
    it writes anything, so an interpreter that cannot run now fails the resolution first
    and never reaches the write this journey is about.
    """
    recipe = _detached_recipe(tmp_path)
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    root = tmp_path / "plans"
    (root / "projects" / "half-written.md").mkdir(parents=True)
    working = tmp_path / "working"
    working.mkdir()

    refused = subprocess.run(  # noqa: S603 - the real script, over a root it cannot write into
        [str(recipe), str(brief), "--name", "half-written"],
        cwd=working,
        env=_environment(root),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert refused.returncode != 0, refused.stdout
    assert "could not be written" in refused.stderr, refused.stderr
    assert not (root / "tasks" / "half-written").exists(), (
        "the task records of a half-written plan were left where the next launch would "
        f"read them:\n{refused.stderr}"
    )
    # And what it could not take is named, because the one thing worse than a partial
    # plan left behind is one left behind in silence.
    assert "could not be removed" in refused.stderr, refused.stderr


#: The one statement of the document's shape, which `just finish-plan` lends its
#: `design-doc` node one criterion out of when it composes that node's task.
DESIGN_TEMPLATE = "config/design-doc-template.md"

#: The pair of markers in that file bounding the criterion the flow lends the dispatch,
#: and the words its refusal carries when a template lends none. Stated here rather than
#: read out of `scripts/finish-plan.sh`, for the reason every expectation in this suite is:
#: a test that took its expectation from its subject would agree with whatever the subject
#: said.
LIFT_OPEN = "<!-- composed-into-the-dispatch -->"
LIFT_CLOSE = "<!-- end composed-into-the-dispatch -->"
#: What a refusal about those markers says, whichever of them fired. Each shape below also
#: has to say which one it was, and `Damaged.diagnosed` is where that is held; this is the
#: words they share, and it is here so one journey can assert their *absence* — a template
#: that is not a file at all must not be reported as one whose markers moved.
LENDS_NOTHING = "lends the design-doc dispatch no criterion"
UNREADABLE = "the design document's template is not readable"


class Damaged(NamedTuple):
    """One way the design document's template can stop lending a whole criterion."""

    #: What went wrong with it, for the parametrised id and the failure message.
    what: str
    #: That template's text, rewritten from the shipped one.
    rewrite: Callable[[str], str]
    #: The words the refusal has to carry for this shape and no other. Whoever reads it
    #: has to find a span in a file they did not damage, so a refusal that named only what
    #: every shape shares — that nothing was lent — would send them looking for missing
    #: prose when what is really there is a second block, or a close above its open. Each
    #: is the shortest span that cannot survive the diagnosis being merged back into one
    #: sentence.
    diagnosed: str


def _emptied(text: str) -> str:
    """That template with its lent block present and holding nothing."""
    opened = text.index(LIFT_OPEN)
    closed = text.index(LIFT_CLOSE)
    return text[: opened + len(LIFT_OPEN) + 1] + text[closed:]


def _blanked(text: str) -> str:
    """That template with its lent block holding lines, none of them carrying anything.

    A separate shape from the empty one because it is what deleting a criterion actually
    leaves: the markers stay, the indentation the bullet sat on stays, and a check that
    asked only whether a line arrived between the two would lend a task an empty bullet
    under a heading promising the property a plan across repositories turns on.
    """
    opened = text.index(LIFT_OPEN)
    closed = text.index(LIFT_CLOSE)
    return text[: opened + len(LIFT_OPEN) + 1] + "\n  \n\t\n" + text[closed:]


#: Every shape the flow has to refuse. Each names a *different span* from the one whoever
#: edited that file meant, which is why they are separate cases: a check asking only
#: whether a marker went past would compose several of them silently.
DAMAGED = (
    Damaged(
        what="lends nothing, because both markers are gone",
        rewrite=lambda text: text.replace(f"{LIFT_OPEN}\n", "").replace(f"{LIFT_CLOSE}\n", ""),
        diagnosed="carries neither",
    ),
    Damaged(
        what="never closes the block it opened",
        rewrite=lambda text: text.replace(f"{LIFT_CLOSE}\n", ""),
        diagnosed="never closes it, so the span it lends runs to the end of the file",
    ),
    Damaged(
        what="opens a second block, so two spans claim to be the criterion",
        rewrite=lambda text: text.replace(
            f"{LIFT_CLOSE}\n", f"{LIFT_CLOSE}\n{LIFT_OPEN}\n- A second one.\n{LIFT_CLOSE}\n"
        ),
        diagnosed="more than one span claims to be the criterion",
    ),
    Damaged(
        what="closes a block before it opens one",
        rewrite=lambda text: text.replace(f"{LIFT_OPEN}\n", f"{LIFT_CLOSE}\n{LIFT_OPEN}\n"),
        diagnosed="before any",
    ),
    Damaged(
        what="lends an empty block",
        rewrite=_emptied,
        diagnosed="holds nothing but whitespace between",
    ),
    Damaged(
        what="lends a block holding only whitespace",
        rewrite=_blanked,
        diagnosed="holds nothing but whitespace between",
    ),
)


def _detached_tail(tmp_path: Path) -> Path:
    """A copy of the tail's own script, the grammar it reads, and the template it lifts from.

    Smaller than the copy above because these refusals come before anything needing a
    store, an interpreter or a credential. That ordering is as much the subject as the
    diagnosis: paying for a review turn first would be paying it for nothing.
    """
    checkout = tmp_path / "tail"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    for name in ("finish-plan.sh", "plan-brief.sh"):
        copied = scripts / name
        copied.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        copied.chmod(0o755)
    template = checkout / DESIGN_TEMPLATE
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_bytes((REPO_ROOT / DESIGN_TEMPLATE).read_bytes())
    return scripts / "finish-plan.sh"


def _tail_environment(tmp_path: Path) -> dict[str, str]:
    """The environment one refused tail runs in, over a run ledger of its own.

    The ledger is stated because the tail asks whether its own run id is taken before it
    reads the template, and a journey that inherited this host's would be refused for a
    run somebody else launched rather than for the template it damaged.
    """
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    return {
        **{key: value for key, value in os.environ.items() if key not in INHERITED},
        "ONEPIPELINE_RUNS_DIR": str(runs),
    }


def _refused_tail(tmp_path: Path, rewrite: Callable[[str], str] | None) -> RefusedTail:
    """Run the real tail over a checkout whose template ``rewrite`` has damaged."""
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    recipe = _detached_tail(tmp_path)
    template = recipe.parent.parent / DESIGN_TEMPLATE
    lent = template.read_text(encoding="utf-8")
    assert lent.count(LIFT_OPEN) == 1 and lent.count(LIFT_CLOSE) == 1, (
        f"the copied {DESIGN_TEMPLATE} does not carry exactly one pair of markers, so "
        "this journey would damage a template that lends nothing already"
    )
    if rewrite is None:
        template.unlink()
    else:
        template.write_text(rewrite(lent), encoding="utf-8")
    # llmlint: ignore-block[tests_mirror_real_usage] The documented surface is a
    # pass-through: `just finish-plan *args` is `@./scripts/finish-plan.sh "$@"` and
    # carries no logic of its own, and that delegation is driven as a real recipe in
    # `tests/e2e/test_delegated_recipes_e2e.py`'s table, so nothing is doubled or
    # bypassed by reaching the script here. What these journeys are about is the tail's
    # refusal *ordering* in a checkout that is deliberately not this one: `_detached_tail`
    # copies that script, the grammar it reads and the template, and nothing else, so
    # there is no `justfile` to invoke — copying one would make the journey about this
    # checkout, which is the premise it exists to avoid. It is this file's own convention
    # too: the refusals above run `_detached_recipe`'s script exactly this way.
    return RefusedTail(
        run=subprocess.run(  # noqa: S603 - the real script, over a template it cannot lift from
            [str(recipe), str(brief)],
            cwd=tmp_path,
            env=_tail_environment(tmp_path),
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        ),
        template=template,
    )
    # llmlint: ignore-end[tests_mirror_real_usage]


class RefusedTail(NamedTuple):
    """One refused tail, and the template it was refused over."""

    run: subprocess.CompletedProcess[str]
    template: Path


@pytest.mark.parametrize("damaged", DAMAGED, ids=lambda damaged: damaged.what)
def test_a_template_that_lends_no_criterion_is_refused_before_the_flow_spends_anything(
    tmp_path: Path, damaged: Damaged
) -> None:
    """A dispatch composed without the lent criterion is what this refusal exists to stop.

    A template whose markers no longer bound one span lends nothing or lends the wrong
    bytes, the task composes, and the flow is an ordinary success that dispatched a worker
    nobody told — passing every other check it makes. Hence a refusal per shape.

    Driven over a copy of the *shipped* template rather than a stub, so a repository that
    stopped carrying the markers fails here rather than leaving these journeys damaging
    something already broken.
    """
    refused = _refused_tail(tmp_path, damaged.rewrite)

    assert refused.run.returncode != 0, refused.run.stdout
    assert DESIGN_TEMPLATE in refused.run.stderr, refused.run.stderr
    assert damaged.diagnosed in refused.run.stderr, (
        f"the refusal does not say the template {damaged.what}, so its reader is left to "
        f"find that span in a file somebody else damaged:\n{refused.run.stderr}"
    )
    # Nothing after the template read has run, which is the other half of this refusal:
    # the review below it spends a real judged turn, and a flow that discovered a template
    # it could not lift from afterwards would have paid for one to learn it.
    assert "review" not in refused.run.stdout.lower(), refused.run.stdout


def test_a_template_that_cannot_be_read_is_refused_before_the_flow_spends_anything(
    tmp_path: Path,
) -> None:
    """A checkout without that file is a different failure from one whose markers moved.

    Separate refusals because they have separate repairs — run from a checkout that has the
    file, against put the markers back. Unnamed, this one is `awk`'s complaint about a path
    and an exit status, which says neither.
    """
    refused = _refused_tail(tmp_path, None)

    assert refused.run.returncode != 0, refused.run.stdout
    assert UNREADABLE in refused.run.stderr, refused.run.stderr
    assert DESIGN_TEMPLATE in refused.run.stderr, refused.run.stderr


def test_a_template_that_is_not_a_file_is_refused_before_the_flow_spends_anything(
    tmp_path: Path,
) -> None:
    """A directory at that path is readable, and reading it yields no criterion at all.

    The same refusal as the missing file above, deliberately, but driven separately: a
    directory is the one shape that passes a readability test and then fails the read, so
    left to that test it would be reported as a template lending no criterion — sending its
    reader to put markers back into a file that is not there.
    """
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    recipe = _detached_tail(tmp_path)
    template = recipe.parent.parent / DESIGN_TEMPLATE
    template.unlink()
    template.mkdir()

    # llmlint: ignore-block[tests_mirror_real_usage] The documented surface is a
    # pass-through: `just finish-plan *args` is `@./scripts/finish-plan.sh "$@"` and
    # carries no logic of its own, and that delegation is driven as a real recipe in
    # `tests/e2e/test_delegated_recipes_e2e.py`'s table, so nothing is doubled or
    # bypassed by reaching the script here. What these journeys are about is the tail's
    # refusal *ordering* in a checkout that is deliberately not this one: `_detached_tail`
    # copies that script, the grammar it reads and the template, and nothing else, so
    # there is no `justfile` to invoke — copying one would make the journey about this
    # checkout, which is the premise it exists to avoid. It is this file's own convention
    # too: the refusals above run `_detached_recipe`'s script exactly this way.
    refused = subprocess.run(  # noqa: S603 - the real script, over a template it cannot read
        [str(recipe), str(brief)],
        cwd=tmp_path,
        env=_tail_environment(tmp_path),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    # llmlint: ignore-end[tests_mirror_real_usage]

    assert refused.returncode != 0, refused.stdout
    assert UNREADABLE in refused.stderr, refused.stderr
    assert LENDS_NOTHING not in refused.stderr, (
        "a directory at the template's path was reported as a template whose markers "
        f"moved, which sends its reader to edit a file that is not there:\n{refused.stderr}"
    )
