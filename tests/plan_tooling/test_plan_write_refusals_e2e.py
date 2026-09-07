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
from pathlib import Path

import plan_root_variable
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
