"""The success hook discards a plan's spike branches; the failure hook keeps them.

A plan's spikes keep their branches on their origins, named `<prefix>/<plan>/spike-<topic>`,
until the plan's main run succeeds, because a failed run's retries and recovery may still
need them. So these launches run a real plan through `just orchestrate` over a scratch
identity whose real Git origin already holds that plan's spike branches — each one cut,
committed, preserved and released through the pinned `onevcs` CLI exactly as a spike
node's session leaves it — beside branches that are not that plan's spikes, and read what
the real hook left on the origin and in every local holder.

Everything between the recipe and the model is real, as in the module beside this one: the
recipe, the wrapper, the installed engine, `scripts/run-ended.sh`, `just reclaim-branch`,
the pinned `onevcs`, `just follow-ups` and the follow-up run it launches. The plan's
lifecycle node declares `expects_no_diff`, so the engine settles it without a dispatch and
the plan still names the repository a spike measured; its direct node is answered by the
paid model's stand-in, which is all that is doubled.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from conftest import git
from nx_workspace import SHARED_TOOLCHAIN_GROUP
from project_fixtures import project_from_plan
from scratch_identity import GIT_IDENTITY, Identity, seeded
from test_run_end_hooks_e2e import (
    LAUNCHED,
    UNCLAIMED_PERSONA,
    Bench,
    Launch,
    _bench,
    _draft,
    _hook,
    _just,
    _settlement,
    _stop,
)
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A real launch holds this checkout's toolchain for as long as it runs.
pytestmark = pytest.mark.xdist_group(SHARED_TOOLCHAIN_GROUP)

#: The pinned `onevcs`, which every spike branch here is cut, preserved and released by.
ONEVCS = REPO_ROOT / ".venv" / "bin" / "onevcs"

#: The host branch prefix every branch here is cut under, separator included, as a host's
#: `branches.yml` states it.
PREFIX = "nick/"

#: The two spike branches the success hook discards, and the one it cannot: checked out,
#: with edits, in the identity's execution checkout, which `onevcs reclaim --discard` refuses
#: to delete. Its topic sorts between the other two, so the listing reaches it in the middle.
DISCARDED = ("spike-a", "spike-b")
HELD = "spike-ab"


class Seeded(NamedTuple):
    """One launch's scratch identity and the branches its origin held before the launch.

    ``run`` is the run the plan launches as, and ``plan`` the native id of the project it is
    launched from, which is what its spike branches are named by.
    """

    identity: Identity
    run: str
    plan: str
    spikes: tuple[str, ...]
    held: str
    unrelated: tuple[str, ...]


class Ended(NamedTuple):
    """A hooked launch over a seeded identity, read back once it ended.

    ``unreachable`` is a second repository the plan changes, registered beside the first,
    whose origin is gone by the time the run ends, so its spikes cannot be listed.
    """

    seeded: Seeded
    unreachable: Path
    result: subprocess.CompletedProcess[str]
    log: str
    remote: frozenset[str]
    local: frozenset[str]


def _onevcs(environment: dict[str, str], *arguments: str) -> str:
    done = subprocess.run(
        [str(ONEVCS), *arguments],
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert done.returncode == 0, f"onevcs {' '.join(arguments)}:\n{done.stdout}\n{done.stderr}"
    return done.stdout


def _spike(environment: dict[str, str], identity: Identity, plan: str, spike: str) -> str:
    """Leave ``spike``'s branch the way a spike node does, and answer its name."""
    opened = json.loads(
        _onevcs(
            environment,
            "session",
            "open",
            str(identity.publication),
            "--branch-name",
            f"{plan}/{spike}",
            "--branch-prefix",
            PREFIX,
            "--label",
            f"run={plan}-spikes",
            "--label",
            f"node={spike}",
        )
    )
    worktree = Path(opened["worktree"])
    (worktree / "harness.sh").write_text(f"echo measuring {spike}\n", encoding="utf-8")
    git("add", "-A", cwd=worktree)
    git(*GIT_IDENTITY, "commit", "-qm", f"feat: {spike} harness", cwd=worktree)
    _onevcs(environment, "preserve", "--repo", str(identity.publication), opened["branch"])
    _onevcs(environment, "session", "close", opened["token"])
    return str(opened["branch"])


def _pushed(identity: Identity, branch: str) -> str:
    """A branch that is no spike of the plan, pushed from the publication checkout."""
    git("checkout", "-q", "-b", branch, cwd=identity.publication)
    (identity.publication / "other.txt").write_text(f"{branch}\n", encoding="utf-8")
    git("add", "-A", cwd=identity.publication)
    git(*GIT_IDENTITY, "commit", "-qm", "feat: unrelated", cwd=identity.publication)
    git("push", "-q", "origin", branch, cwd=identity.publication)
    git("checkout", "-q", "main", cwd=identity.publication)
    return branch


def _seeded(bench: Bench, identity: Identity, run: str, plan: str) -> Seeded:
    spikes = tuple(_spike(bench.environment, identity, plan, spike) for spike in DISCARDED)
    held = _spike(bench.environment, identity, plan, HELD)
    git("fetch", "-q", "origin", cwd=identity.execution)
    git("checkout", "-q", "-b", held, f"origin/{held}", cwd=identity.execution)
    (identity.execution / "harness.sh").write_text("echo edited\n", encoding="utf-8")
    unrelated = (
        _pushed(identity, f"{PREFIX}{plan}/build-listing"),
        _pushed(identity, f"{PREFIX}{plan}-later/spike-a"),
    )
    return Seeded(identity, run, plan, spikes, held, unrelated)


def _remote(identity: Identity) -> frozenset[str]:
    listed = git("ls-remote", "--heads", "origin", cwd=identity.publication)
    return frozenset(line.split("\trefs/heads/", 1)[1] for line in listed.splitlines())


def _local(identity: Identity) -> frozenset[str]:
    """Every branch any clone of the identity holds: its two checkouts and every session's."""
    holders = [identity.publication, identity.execution]
    holders += [path.parent for path in identity.home.rglob("HEAD") if path.parent.name == ".git"]
    holders += [path.parent for path in identity.home.rglob("clone/HEAD")]
    found: set[str] = set()
    for holder in holders:
        listed = git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=holder)
        found.update(listed.split())
    return frozenset(found)


def _launch(bench: Bench, run: str, **direct: object) -> Ended:
    """Launch a plan over a seeded identity whose origin holds its spikes, a follow-up drafted.

    The project is written first, because its native id is what the spikes' branches are
    named by; they are cut, preserved and released before anything is launched.
    """
    identity = seeded(bench.tmp / "identity")
    bench.environment["ONEVCS_HOME"] = str(identity.home)
    unreachable = _unreachable(bench)
    plan = bench.tmp / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "Build what the spikes measured"},
                "name": run,
                "tasks": [
                    {
                        "id": "report",
                        "persona": "engineer",
                        "task": "Report without changing files.\n\n## Acceptance criteria\n"
                        "- Reported.",
                        **direct,
                    },
                    {
                        "id": "listing",
                        "repo": str(identity.publication),
                        "title": "feat: page the listing",
                        "expects_no_diff": True,
                        "task": "The listing already pages.\n\n## Acceptance criteria\n- It pages.",
                    },
                    {
                        "id": "elsewhere",
                        "repo": str(unreachable),
                        "title": "feat: page the listing elsewhere",
                        "expects_no_diff": True,
                        "task": "It already pages there.\n\n## Acceptance criteria\n- It pages.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    project = project_from_plan(plan, run)
    seeded_ = _seeded(bench, identity, run, project.partition(":")[2])
    _draft(bench, run)
    shutil.rmtree(unreachable.parent / f"{unreachable.name}-origin.git")
    result = _just(bench, "orchestrate", project, "--dag-graph", "off")
    hooks = bench.runs / run / "hooks"
    log = "".join(
        (hooks / name).read_text(encoding="utf-8")
        for name in ("success.log", "failure.log")
        if (hooks / name).is_file()
    )
    return Ended(seeded_, unreachable, result, log, _remote(identity), _local(identity))


def _unreachable(bench: Bench) -> Path:
    """A second repository registered in the same scratch registry, with an origin of its own."""
    root = bench.tmp / "unreachable"
    root.mkdir()
    origin, checkout = root / "elsewhere-origin.git", root / "elsewhere"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("clone", "-q", str(origin), str(checkout))
    (checkout / "README.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=checkout)
    git(*GIT_IDENTITY, "commit", "-qm", "chore: seed", cwd=checkout)
    git("push", "-q", "origin", "main", cwd=checkout)
    _onevcs(bench.environment, "register", str(checkout))
    return checkout


@pytest.fixture(scope="module")
def succeeded(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Ended:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path_factory.mktemp("spike-discard-success"), oneharness_bin)
    run = f"spiked-{os.getpid()}"
    follow_up = ""
    try:
        ended = _launch(bench, run)
        matched = LAUNCHED.search(ended.log)
        follow_up = matched["follow_up"] if matched else ""
        return ended
    finally:
        _stop(bench, run, *([follow_up] if follow_up else []))


@pytest.fixture(scope="module")
def failed(tmp_path_factory: pytest.TempPathFactory, oneharness_bin: str) -> Ended:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    bench = _bench(tmp_path_factory.mktemp("spike-discard-failure"), oneharness_bin)
    run = f"spiked-failed-{os.getpid()}"
    try:
        # A persona no built-in role claims fails its node in config validation, without a
        # turn, so the run ends failed with the lifecycle node beside it done.
        return _launch(bench, run, persona=UNCLAIMED_PERSONA)
    finally:
        _stop(bench, run)


def test_a_success_ending_discards_the_plans_spike_branches_from_the_origin_and_every_holder(
    succeeded: Ended,
) -> None:
    seeded_ = succeeded.seeded
    assert _settlement_of(succeeded) == "complete", succeeded.result.stderr

    for spike in seeded_.spikes:
        assert spike not in succeeded.remote, f"{spike} is still on the origin: {succeeded.log}"
        assert spike not in succeeded.local, f"{spike} is still held locally: {succeeded.local}"
    for kept in seeded_.unrelated:
        assert kept in succeeded.remote, f"{kept}, which is no spike of the plan, was deleted"


def test_a_success_ending_reports_each_discard_and_the_branch_it_could_not_discard(
    succeeded: Ended,
) -> None:
    seeded_, log = succeeded.seeded, succeeded.log

    (discarded,) = [line for line in log.splitlines() if "discarded the spike branches" in line]
    for spike in seeded_.spikes:
        assert f"{spike} of {seeded_.identity.publication}" in discarded, discarded
    assert (
        f"run-ended: spike branch {seeded_.held} of {seeded_.identity.publication} could not "
        "be discarded ('just reclaim-branch' exited "
    ) in log, log
    assert "checked-out" in log, log
    assert seeded_.held in succeeded.remote, "the branch it could not discard was deleted anyway"
    # The branch it could not discard sits between the other two in the listing, so the one
    # after it being gone is what shows the refusal stopped nothing.
    assert seeded_.spikes[1] not in succeeded.remote, succeeded.log


def test_a_success_ending_still_launches_the_follow_up_run_after_its_discards(
    succeeded: Ended,
) -> None:
    log = succeeded.log
    matched = LAUNCHED.search(log)

    assert matched is not None, f"the success hook launched no follow-up run:\n{log}"
    assert matched["run"] == succeeded.seeded.run
    assert log.index("run-ended: discarded the spike branches") < matched.start(), log


def test_a_failure_ending_discards_no_spike_branch(failed: Ended) -> None:
    seeded_ = failed.seeded
    assert _settlement_of(failed) != "complete", failed.result.stdout

    assert "ended without every node done" in failed.log, failed.log
    assert "spike branch" not in failed.log, failed.log
    for kept in (*seeded_.spikes, seeded_.held, *seeded_.unrelated):
        assert kept in failed.remote, f"the failure hook deleted {kept}: {failed.log}"
    for spike in seeded_.spikes:
        assert spike in failed.local, f"the failure hook deleted {spike} locally"


def _settlement_of(ended: Ended) -> object:
    return _settlement(Launch(ended.result, ended.seeded.run))["settlement"]


def _spike_hook(tmp_path: Path, run_root: Path) -> subprocess.CompletedProcess[str]:
    """The success hook over ``run_root``, spawned as the engine spawns it, on its own bench."""
    return _hook(
        tmp_path,
        {
            "ONEVCS_HOME": str(tmp_path / "onevcs"),
            "ONEPIPELINE_HOOK": "success",
            "ONEPIPELINE_RUN_ID": run_root.name,
            "ONEPIPELINE_RUN_ROOT": str(run_root),
        },
    )


#: The success hook's line once it has asked `just follow-ups`, which the drafts decide.
FOLLOWED_UP = "follow-ups: run "


# llmlint: ignore[tests_mirror_real_usage] The hook is spawned exactly as the engine spawns it; what no launch produces on demand is a run whose plan cannot be read, which reaches this hook in life as a launch record a disk or a hand removed, so the run root here holds none — the shape the module beside this drives its nothing-to-verify ending through.  # noqa: E501 - a directive is one line
def test_a_run_whose_spikes_cannot_be_listed_still_reaches_its_follow_up_run(
    tmp_path: Path,
) -> None:
    """No record to list by is said on the hook's log, and the hook goes on to follow up.

    Driven by spawning the hook as the engine does, over a run root holding no launch record,
    which no launch produces: the same shape the module beside this drives the
    nothing-to-verify ending through.
    """
    run_root = tmp_path / "runs" / f"unlistable-{os.getpid()}"

    ran = _spike_hook(tmp_path, run_root)

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "could not be listed (status 2), so none was discarded" in ran.stderr, ran.stderr
    assert ran.stdout.startswith(FOLLOWED_UP), ran.stdout


def test_a_repository_whose_spikes_cannot_be_listed_is_named_and_stops_nothing(
    succeeded: Ended,
) -> None:
    """One repository whose origin is gone is said with its repair; the rest go on."""
    log, repository = succeeded.log, str(succeeded.unreachable)

    (unlisted,) = [line for line in log.splitlines() if "could not be listed" in line]
    assert f"on {repository} could not be listed, so none there was discarded" in unlisted
    assert f"'just reclaim-branch <branch> --repo {repository} --discard'" in unlisted
    assert "discarded the spike branches" in log, log
    assert LAUNCHED.search(log) is not None, log
