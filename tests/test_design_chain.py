"""Which repository's layer a plan's design document resolves through, decided once.

`orchestrator/design_chain.py` is the one statement of the rule both the writer's flow
(`scripts/finish-plan.sh`) and the approval (`orchestrator/design_approval.py`) read: a
plan whose tasks all name exactly one and the same repository resolves through
`--repository <that origin>`, and every other plan — tasks naming several repositories, or
none — names no repository, so the working directory's layer answers.

Each case is read out of a real local plan store through the pinned plan-store CLI, the
way both callers read it. The journeys that hold the two callers to it are
`tests/plan_tooling/test_finish_plan_recipe_e2e.py` and
`tests/plan_tooling/test_approve_design_recipe_e2e.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import design_chain

#: The local source each plan here is written into, configured through the environment
#: layer so the pinned CLI the SDK spawns reads it.
SOURCE = "chained"

#: Two normalized origins, so a plan can name one, the other, or both.
ONE = "github.com/example/service"
OTHER = "github.com/example/client"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real, empty local source this test's processes and the CLI they spawn both read."""
    root = tmp_path / "store"
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__PLUGIN", "local-md")
    monkeypatch.setenv(f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__CONFIG__ROOT", str(root))
    return root


def _plan(root: Path, project: str, *tasks: tuple[str, ...]) -> str:
    """Write ``project`` into ``root``, one task per entry of ``tasks`` naming those repositories.

    Written as the local store's own records, because a task naming several repositories is
    one no plan writer here composes and is still a plan the rule has to answer for.
    """
    (root / "projects").mkdir(parents=True)
    (root / "projects" / f"{project}.md").write_text(
        f'---\ntitle: "{project}"\nstatus: "todo"\n---\n\nThe plan.\n', encoding="utf-8"
    )
    (root / "tasks" / project).mkdir(parents=True)
    for index, repositories in enumerate(tasks):
        named = (
            "repositories:\n" + "".join(f'  - "{origin}"\n' for origin in repositories)
            if repositories
            else ""
        )
        (root / "tasks" / project / f"task-{index}.md").write_text(
            f'---\ntitle: "task {index}"\nproject: "{project}"\nstatus: "todo"\n{named}---\n\n'
            "The task.\n",
            encoding="utf-8",
        )
    return f"{SOURCE}:{project}"


@pytest.mark.parametrize(
    ("tasks", "chosen"),
    [
        (((ONE,), (ONE,)), ONE),
        (((ONE,),), ONE),
        (((ONE,), (OTHER,)), None),
        (((ONE, OTHER),), None),
        (((ONE,), ()), None),
        (((), ()), None),
        ((), None),
    ],
    ids=[
        "one-repository",
        "one-task",
        "several",
        "one-task-naming-two",
        "one-and-none",
        "none",
        "empty",
    ],
)
def test_a_plan_resolves_through_its_one_repository_and_no_other(
    store: Path, tasks: tuple[tuple[str, ...], ...], chosen: str | None
) -> None:
    project = _plan(store, "demo", *tasks)
    assert design_chain.plan_repository(project) == chosen


def test_the_resolve_command_names_the_repository_only_when_the_rule_picks_one() -> None:
    assert design_chain.resolve_command(ONE) == (
        f"onepipeline template resolve design-doc --repository {ONE} --json"
    )
    assert design_chain.resolve_command(None) == "onepipeline template resolve design-doc --json"


def test_the_flow_reads_the_command_off_the_module_it_shares_with_the_approval(
    store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`scripts/finish-plan.sh` runs this entry point; what it prints is the whole answer."""
    project = _plan(store, "single", (ONE,), (ONE,))
    assert design_chain.main([project]) == 0
    assert capsys.readouterr().out == design_chain.resolve_command(ONE) + "\n"


def test_a_plan_the_store_cannot_answer_for_is_refused_by_name(
    store: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del store
    assert design_chain.main(["nowhere:missing"]) == 1
    assert capsys.readouterr().err.startswith("design-chain: ")
