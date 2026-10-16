"""A manager landing's events reach two audiences: the owner's default watch, and detail.

The pinned engine relays every event the `onevcs` operation records into the landing's
journal with the producer's own significance. Under the shipped `planner` profile — what a
bare `just watch --flow` and `just monitor --flow` read — only milestones and blockers
reach the owner: a change opened or drafted, its checks settled, one notice of the work
reaching its base, and the final result; every refusal, required check failing, conflict
and death. Everything else is progress, read with `--filter detailed`. This journey drives
both audiences through the real recipes for each landing verb:

- a hosted publication and recovery, whose host reports required checks the journey moves:
  the default watch wakes on the change opening and drafting, stays armed through every
  check's movement while the detailed reader takes it, then wakes on the checks settling —
  and a required check failing wakes it at once while another is still pending;
- an integration train of three branches, which has no hosted checks: its lock, gate and
  push progress reach only the detailed reader, and the train reaching its base wakes the
  owner once, not once per member;
- two trains of one repository overlapping, each with its own prepared handle and every
  relayed event carrying it alone;
- a landing of a branch a live run opened, whose events that run's own detailed reader
  receives, mirrored, while the landing's flow keeps them too.

GitHub is `tests/e2e/fake_gh.py` at `onevcs`'s `ONEVCS_GH` seam, and the originating run's
paid model is the doubled provider every launch journey uses; nothing else is substituted.
"""

# The finding these answer is which Nx project owns this file: `publish-and-landing`, the
# directory-owned project whose key is the recipes, wrappers and configs every landing
# journey here drives, and which declares no marker tier.
# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] see above
# llmlint: ignore-file[shell_test_tiers_stay_split] see above

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import git
from manager_landings import (
    ORIGINATOR,
    WATCH_ELAPSED,
    WATCH_FLOW_FAILED,
    Armed,
    Estate,
    HeldReport,
    Hosted,
    Identity,
    Landing,
    Returned,
    Session,
    estate,
    held_identity,
    hosted_identity,
    originating_run,
    watch,
    woken_until_ended,
)
from scratch_identity import GIT_IDENTITY
from waits import timeout as e2e_timeout
from waits import until

from orchestrator.root import REPO_ROOT


@pytest.fixture
def the_estate(tmp_path: Path, oneharness_bin: str) -> Iterator[Estate]:
    """A fresh estate per journey, whose landings and live runs are reaped however it ends."""
    built = estate(tmp_path, oneharness_bin)
    yield built
    built.close()


OWNER = Session("manager-owner")
SECOND = Session("manager-second")
BRANCH = "feature/widget"
#: The host's checks when the change opens: two required, one advisory, none settled.
OPENING_CHECKS = (
    "lint in_progress - required",
    "unit queued - required",
    "docs queued - advisory",
)


def _detail_until_quiet(
    the_estate: Estate, session: Session, identity: Identity, flow: str, cursor: str | None
) -> tuple[list[Returned], str | None]:
    """Hand the detailed reader everything recorded so far; every return and the cursor."""
    taken: list[Returned] = []
    while True:
        at = ["--cursor", cursor] if cursor else []
        detailed = watch(
            the_estate, session, identity, flow, "--filter", "detailed", "--timeout", "0", *at
        )
        if detailed.exit == WATCH_ELAPSED or detailed.condition in ("ended", "flow-failed"):
            # Caught up with what is recorded, or with the flow's own ending.
            return taken, cursor
        assert detailed.condition == "landing-progress", detailed.line
        taken.append(detailed)
        cursor = detailed.cursor


def _branch_with_work(checkout: Path, branch: str, subject: str) -> None:
    git("checkout", "-q", "-b", branch, cwd=checkout)
    (checkout / f"{branch.replace('/', '-')}.txt").write_text("work\n", encoding="utf-8")
    git("add", "-A", cwd=checkout)
    git(*GIT_IDENTITY, "commit", "-qm", subject, cwd=checkout)
    git("checkout", "-q", "main", cwd=checkout)


def _onevcs(the_estate: Estate, identity: Identity, *arguments: str) -> None:
    ran = subprocess.run(
        ["uv", "run", "onevcs", *arguments],
        cwd=REPO_ROOT,
        env=the_estate.as_manager(OWNER, identity),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr


def _hosted_landing(the_estate: Estate, hosted: Hosted, verb: str) -> Landing:
    checkout = hosted.identity.publication
    if verb == "publish-branch":
        _branch_with_work(checkout, BRANCH, "feat: add the widget")
        arguments = [verb, BRANCH, "--repo", str(checkout), "--no-draft"]
    else:
        _branch_with_work(checkout, BRANCH, "feat: add the widget (incomplete step)")
        _onevcs(the_estate, hosted.identity, "preserve", BRANCH, "--repo", str(checkout))
        arguments = [
            verb,
            BRANCH,
            "--repo",
            str(checkout),
            "--title",
            "feat: add the widget",
            "--no-draft",
        ]
    return Landing(the_estate, OWNER, hosted.identity, arguments)


def _opened_and_drafted(the_estate: Estate, hosted: Hosted, landing: Landing) -> Returned:
    """The two milestones a `change-auto` landing opens with, each waking the default watch."""
    opened = watch(the_estate, OWNER, hosted.identity, landing.flow, "--timeout", "600")
    assert (opened.kind, opened.significance) == ("change-opened", "milestone"), opened.line
    drafted = watch(
        the_estate,
        OWNER,
        hosted.identity,
        landing.flow,
        "--timeout",
        "600",
        "--cursor",
        opened.cursor,
    )
    assert (drafted.kind, drafted.significance) == ("change-drafted", "milestone"), drafted.line
    return drafted


@pytest.mark.parametrize("verb", ["publish-branch", "repo-recover"])
def test_a_hosted_landing_wakes_its_owner_on_opened_and_settled_checks_and_not_on_their_progress(
    the_estate: Estate, verb: str
) -> None:
    hosted = hosted_identity(the_estate, "hosted", *OPENING_CHECKS)
    landing = _hosted_landing(the_estate, hosted, verb)
    drafted = _opened_and_drafted(the_estate, hosted, landing)
    landing.await_recorded("change-check", name="lint", state="pending")

    # Armed past the drafting, the default watch waits through a check passing, which the
    # detailed reader is handed.
    armed = Armed(the_estate, OWNER, hosted.identity, landing.flow, "--cursor", drafted.cursor)
    hosted.report(
        "lint completed success required",
        "unit in_progress - required",
        "docs queued - advisory",
    )
    landing.await_recorded("change-check", name="lint", state="passed")
    taken, _ = _detail_until_quiet(the_estate, OWNER, hosted.identity, landing.flow, drafted.cursor)
    assert any(wake.kind == "change-check" for wake in taken), [wake.line for wake in taken]
    assert {wake.significance for wake in taken} == {"progress"}, [w.line for w in taken]
    assert armed.waiting(), "the owner's default watch woke on a check's progress"

    # Every check passes and the host may land what it armed: the next wake is the checks
    # settling, a distinct milestone.
    hosted.merge_permit.parent.mkdir(parents=True, exist_ok=True)
    hosted.merge_permit.touch()
    hosted.report(
        "lint completed success required",
        "unit completed success required",
        "docs completed success advisory",
    )
    settled = armed.returned()
    assert (settled.kind, settled.significance) == ("checks-settled", "milestone"), settled.line
    woken, ending = woken_until_ended(
        the_estate, OWNER, hosted.identity, landing.flow, settled.cursor
    )
    assert landing.ended() == 0, landing.said()
    assert ending.condition == "ended", ending.line
    assert {wake.significance for wake in woken} <= {"milestone"}, [w.line for w in woken]
    assert [wake.kind for wake in woken].count("change-merged") == 1, [w.kind for w in woken]

    _every_event_reaches_the_detailed_reader(the_estate, OWNER, hosted.identity, landing)
    planner = the_estate.recipe(OWNER, hosted.identity, "monitor", "--flow", landing.flow)
    assert "change-check" not in planner.stdout, planner.stdout


def _every_event_reaches_the_detailed_reader(
    the_estate: Estate, session: Session, identity: Identity, landing: Landing
) -> None:
    """`just monitor --flow --filter detailed` shows every event the journal holds."""
    shown = the_estate.recipe(
        session, identity, "monitor", "--flow", landing.flow, "--filter", "detailed"
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    recorded = landing.kinds()
    lines = [
        line
        for line in shown.stdout.splitlines()
        if landing.landing in line and not line.startswith("--")
    ]
    assert [kind for kind in recorded if any(f" {kind} " in line for line in lines)] == recorded
    assert len(lines) == len(recorded), (recorded, shown.stdout)


@pytest.mark.parametrize("verb", ["publish-branch", "repo-recover"])
def test_a_required_check_failing_wakes_the_owner_while_another_is_still_pending(
    the_estate: Estate, verb: str
) -> None:
    hosted = hosted_identity(
        the_estate, "hosted", "lint in_progress - required", "unit in_progress - required"
    )
    landing = _hosted_landing(the_estate, hosted, verb)
    drafted = _opened_and_drafted(the_estate, hosted, landing)
    landing.await_recorded("change-check", name="unit", state="pending")
    armed = Armed(the_estate, OWNER, hosted.identity, landing.flow, "--cursor", drafted.cursor)

    hosted.report("lint completed failure required", "unit in_progress - required")
    red = armed.returned()
    assert (red.kind, red.significance) == ("change-check", "blocker"), red.line
    # When it was recorded, the other required check was still pending.
    journal = landing.journal()
    failed_at = next(
        index
        for index, event in enumerate(journal)
        if event.kind == "change-check" and event.payload.get("state") == "failed"
    )
    unit = [
        event.payload.get("state")
        for event in journal[:failed_at]
        if event.kind == "change-check" and event.payload.get("name") == "unit"
    ]
    assert unit and unit[-1] == "pending", unit
    assert "checks-settled" not in [event.kind for event in journal[:failed_at]]

    # The landing then ends refused: every later wake is a blocker, and the flow fails.
    woken, ending = woken_until_ended(the_estate, OWNER, hosted.identity, landing.flow, red.cursor)
    assert landing.ended() != 0, landing.said()
    assert {wake.significance for wake in woken} <= {"blocker"}, [w.line for w in woken]
    assert ending.exit == WATCH_FLOW_FAILED, ending.line


@pytest.mark.parametrize("verb", ["publish-branch", "repo-recover"])
def test_checks_settling_with_a_skipped_required_check_wakes_the_owner_as_a_milestone(
    the_estate: Estate, verb: str
) -> None:
    hosted = hosted_identity(
        the_estate, "hosted", "lint completed success required", "unit completed skipped required"
    )
    hosted.merge_permit.parent.mkdir(parents=True, exist_ok=True)
    hosted.merge_permit.touch()
    landing = _hosted_landing(the_estate, hosted, verb)
    drafted = _opened_and_drafted(the_estate, hosted, landing)

    settled = watch(
        the_estate,
        OWNER,
        hosted.identity,
        landing.flow,
        "--timeout",
        "600",
        "--cursor",
        drafted.cursor,
    )
    assert (settled.kind, settled.significance) == ("checks-settled", "milestone"), settled.line
    [ruling] = [event for event in landing.journal() if event.kind == "checks-settled"]
    assert ruling.payload.get("verdict") == "passed-with-skipped", ruling.payload
    assert ruling.payload.get("skipped") == ["unit"], ruling.payload
    woken, ending = woken_until_ended(
        the_estate, OWNER, hosted.identity, landing.flow, settled.cursor
    )
    assert landing.ended() == 0, landing.said()
    assert ending.condition == "ended", ending.line
    assert [wake.kind for wake in woken] == ["change-merged"], [w.line for w in woken]


def test_a_draft_kept_for_review_wakes_the_owner_as_a_blocker(the_estate: Estate) -> None:
    hosted = hosted_identity(
        the_estate,
        "hosted",
        "lint in_progress - required",
        publication="change-open",
        approvals="required",
    )
    landing = _hosted_landing(the_estate, hosted, "publish-branch")
    drafted = _opened_and_drafted(the_estate, hosted, landing)
    landing.await_recorded("change-check", name="lint", state="pending")
    hosted.report("lint completed success required")

    settled = watch(
        the_estate,
        OWNER,
        hosted.identity,
        landing.flow,
        "--timeout",
        "600",
        "--cursor",
        drafted.cursor,
    )
    assert (settled.kind, settled.significance) == ("checks-settled", "milestone"), settled.line
    held = watch(
        the_estate,
        OWNER,
        hosted.identity,
        landing.flow,
        "--timeout",
        "600",
        "--cursor",
        settled.cursor,
    )
    assert (held.kind, held.significance) == ("draft-kept-for-review", "blocker"), held.line
    assert landing.ended() == 0, landing.said()
    # Kept for its reviewer: the host still holds it as an open draft, never lifted.
    [change] = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in hosted.merge_permit.parent.glob("pr-*.json")
    ]
    assert (change["state"], change["isDraft"]) == ("OPEN", True), change


@pytest.mark.parametrize("verb", ["publish-branch", "repo-recover"])
def test_a_conflict_with_the_moved_base_wakes_the_owner_as_a_blocker(
    the_estate: Estate, verb: str
) -> None:
    service = held_identity(the_estate, "service")
    subject = "feat: rewrite the readme"
    if verb == "repo-recover":
        subject += " (incomplete step)"
    git("checkout", "-q", "-b", BRANCH, cwd=service.publication)
    (service.publication / "README.md").write_text("the branch's readme\n", encoding="utf-8")
    git(*GIT_IDENTITY, "commit", "-qam", subject, cwd=service.publication)
    git("checkout", "-q", "main", cwd=service.publication)
    if verb == "repo-recover":
        _onevcs(the_estate, service, "preserve", BRANCH, "--repo", str(service.publication))
    # The base moves under the branch, on the same line.
    the_estate.hold.release()
    (service.execution / "README.md").write_text("the base's readme\n", encoding="utf-8")
    git(
        *GIT_IDENTITY, "commit", "-qam", "feat: the base rewrites the readme", cwd=service.execution
    )
    git("push", "-q", "origin", "main", cwd=service.execution)
    the_estate.hold.rearm()

    arguments = [verb, BRANCH, "--repo", str(service.publication), "--no-draft"]
    if verb == "repo-recover":
        arguments += ["--title", "feat: rewrite the readme"]
    landing = Landing(the_estate, OWNER, service, arguments)
    # A conflict refuses the landing before its first push, so it can end before any watch
    # arms; the owner's watch reads its flow from the first byte of its journal.
    start = f"flow:2:{landing.flow}:{landing.landing}@0:"
    conflict = watch(
        the_estate, OWNER, service, landing.flow, "--timeout", "600", "--cursor", start
    )
    assert (conflict.kind, conflict.significance) == ("sync-conflict", "blocker"), conflict.line
    [recorded] = [event for event in landing.journal() if event.kind == "sync-conflict"]
    assert recorded.payload.get("paths") == ["README.md"], recorded.payload
    woken, ending = woken_until_ended(the_estate, OWNER, service, landing.flow, conflict.cursor)
    assert landing.ended() != 0, landing.said()
    assert {wake.significance for wake in woken} <= {"blocker"}, [w.line for w in woken]
    assert ending.exit == WATCH_FLOW_FAILED, ending.line
    assert the_estate.hold.arrivals() == 0, "the conflicted landing pushed"


TRAIN = ("train/first", "train/second", "train/third")


def test_an_integration_train_stays_armed_through_its_progress_and_wakes_once_on_its_base(
    the_estate: Estate,
) -> None:
    """Held before its own gate and push, then before its report, the owner wakes on milestones.

    Another manager's train holds the repository first, at that train's push. This train
    registers and starts behind it, and its owner arms the default watch. Released, the
    first train lands; this one takes the repository, merges each of its three members and
    is held at its own push — the first arrival and the second at the repository's
    `pre-push` hook, released one at a time. Everything recorded up to there is progress
    the detailed reader takes while the default watch stays armed. Released, the gate
    passes and the push is accepted.

    `onevcs integrate` records the push, the gate run and the base's arrival in one
    synchronous call with nothing between them, so no moment exists in which the push and
    gate run are recorded and the arrival is not, and no handshake can hold a watch there.
    What proves the owner is not woken by them is the identity of the wake instead: the
    watch armed before the push returns on the arrival, and a detailed read resuming from
    its cursor is handed neither record, so it passed over both; one that woke on either
    would have returned that kind. The detailed reader from the same starting point is
    handed the push and the gate run as separate progress before the arrival. Then the
    train is held writing its report — which it does after the arrival and before the
    engine records its ending — and a default watch re-armed at the arrival is seen to
    wait until the report is released, then returns for the landing's final result, the
    flow's own ending.
    """
    service = held_identity(the_estate, "service")
    for branch in (*TRAIN, "ahead/one"):
        _branch_with_work(service.publication, branch, f"feat: add {branch}")
    ahead = Landing(
        the_estate, SECOND, service, ["integrate", "ahead/one", "--push"], cwd=service.publication
    )
    until(
        "the first train's push to be held",
        lambda: the_estate.hold.arrivals() >= 1,
        seconds=300,
        state=ahead.said,
    )
    report = HeldReport()
    landing = Landing(
        the_estate,
        OWNER,
        service,
        ["integrate", *TRAIN, "--push"],
        cwd=service.publication,
        report=report,
    )
    landing.await_recorded("landing-started")
    armed = Armed(the_estate, OWNER, service, landing.flow)

    the_estate.hold.release_arrival(1)
    assert ahead.ended() == 0, ahead.said()
    until(
        "this train's push to be held",
        lambda: the_estate.hold.arrivals() >= 2,
        seconds=300,
        state=landing.said,
    )
    taken, cursor = _detail_until_quiet(the_estate, OWNER, service, landing.flow, None)
    kinds = [wake.kind for wake in taken]
    assert "lock-acquired" in kinds, kinds
    assert kinds.count("merge-completed") == len(TRAIN), kinds
    assert not {"push", "gate-run", "base-arrived"} & set(kinds), kinds
    assert {wake.significance for wake in taken} == {"progress"}, [w.line for w in taken]
    assert armed.waiting(), "the owner's default watch woke on the train's progress"

    the_estate.hold.release_arrival(2)
    arrived = armed.returned()
    assert (arrived.kind, arrived.significance) == ("base-arrived", "milestone"), arrived.line
    # The wake's cursor is past the push and the gate run: resumed from it, the detailed
    # reader is handed neither.
    past, _ = _detail_until_quiet(the_estate, OWNER, service, landing.flow, arrived.cursor)
    assert not {"push", "gate-run", "base-arrived"} & {wake.kind for wake in past}, [
        wake.line for wake in past
    ]
    # Held writing its report: the train reached its base and its ending is not recorded.
    landing.await_recorded("base-arrived")
    assert landing.process.poll() is None, landing.said()
    assert "landing-finished" not in landing.kinds(), landing.kinds()

    # Re-armed at the arrival, the default watch waits while the train is held; from the
    # cursor the first watch was armed past, the detailed reader is handed the train's
    # successful push and gate run, each its own progress event, before the arrival.
    rearmed = Armed(the_estate, OWNER, service, landing.flow, "--cursor", arrived.cursor)
    after, _ = _detail_until_quiet(the_estate, OWNER, service, landing.flow, cursor)
    following = [wake.kind for wake in after]
    for kind in ("push", "gate-run"):
        assert following.index(kind) < following.index("base-arrived"), following
    assert {wake.significance for wake in after if wake.kind in ("push", "gate-run")} == {
        "progress"
    }
    [gate] = [event for event in landing.journal() if event.kind == "gate-run"]
    assert gate.payload.get("verdict") == "passed", gate.payload
    [pushed] = [event for event in landing.journal() if event.kind == "push"]
    assert pushed.payload.get("accepted") is True, pushed.payload
    assert rearmed.waiting(), "the owner's re-armed default watch woke before the final result"

    # Released, the train reports itself — its members in the order given, pushed — and the
    # re-armed watch's next return is the flow's own ending, its final result: nothing past
    # the arrival woke it, so the train woke its owner on its base once and then on its end.
    reported = report.drain()
    assert landing.ended() == 0, landing.said()
    assert [line.split(":")[0].strip() for line in reported.splitlines()[1:4]] == list(TRAIN)
    assert "Pushed: yes" in reported, reported
    finished = rearmed.returned()
    assert (finished.condition, finished.status) == ("ended", 0), finished.line
    assert "landing-finished" in landing.kinds(), landing.kinds()
    assert landing.kinds().count("base-arrived") == 1, landing.kinds()
    for hosted_kind in ("change-opened", "change-check", "checks-settled"):
        assert hosted_kind not in landing.kinds(), landing.kinds()
    _every_event_reaches_the_detailed_reader(the_estate, OWNER, service, landing)
    # The flow's own reader of the next detail, through the recipe a manager answers with.
    reader = the_estate.recipe(
        OWNER, service, "channel-next", "--flow", landing.flow, "--filter", "detailed"
    )
    assert reader.returncode == 0, reader.stdout + reader.stderr
    assert "landing-registered" in reader.stdout, reader.stdout


def test_two_overlapping_trains_of_one_repository_keep_their_own_handles(
    the_estate: Estate,
) -> None:
    service = held_identity(the_estate, "service")
    for branch in TRAIN:
        _branch_with_work(service.publication, branch, f"feat: add {branch}")
    first = Landing(
        the_estate, OWNER, service, ["integrate", *TRAIN[:2], "--push"], cwd=service.publication
    )
    until(
        "the first train's push to be held",
        lambda: the_estate.hold.arrivals() >= 1,
        seconds=300,
        state=first.said,
    )
    second = Landing(
        the_estate, SECOND, service, ["integrate", TRAIN[2], "--push"], cwd=service.publication
    )
    # Registered and started, it waits for the repository behind the first train.
    second.await_recorded("landing-started")
    # Waiting behind the first train wakes neither owner.
    for landing in (first, second):
        quiet = watch(the_estate, landing.session, service, landing.flow, "--timeout", "0")
        assert quiet.exit == WATCH_ELAPSED, quiet.line
    assert first.flow != second.flow
    assert first.operation() != second.operation()

    the_estate.hold.release()
    assert first.ended() == 0, first.said()
    assert second.ended() == 0, second.said()
    for landing in (first, second):
        relayed = [event for event in landing.journal() if event.source == "vcs"]
        assert relayed, landing.kinds()
        handles = {event.labels.get("operation_id") for event in relayed}
        assert handles == {landing.operation()}, handles


def test_a_live_originating_runs_detailed_reader_receives_the_landings_events(
    the_estate: Estate,
) -> None:
    """The run that opened the branch is still live, and its own reader gets the mirror."""
    service = held_identity(the_estate, "service")
    run = "live-origin"
    left = originating_run(
        the_estate, service, run, {"kept": "feat: add kept"}, waiting_on_a_person=True
    )
    landing = Landing(
        the_estate,
        OWNER,
        service,
        ["publish-branch", left["kept"], "--repo", str(service.execution), "--no-draft"],
    )
    until(
        "the landing's push to be held",
        lambda: the_estate.hold.arrivals() >= 1,
        seconds=300,
        state=landing.said,
    )
    status = the_estate.recipe(ORIGINATOR, service, "status", run, "--no-providers")
    assert "SETTLED" not in status.stdout and "ENDED" not in status.stdout, status.stdout
    the_estate.hold.release()
    assert landing.ended() == 0, landing.said()

    shown = the_estate.recipe(ORIGINATOR, service, "monitor", run, "--filter", "detailed")
    mirrored = [line for line in shown.stdout.splitlines() if landing.operation() in line]
    assert any(" base-arrived" in line for line in mirrored), shown.stdout
    # The flow keeps the same events for its own owner, with no observer of its own.
    _every_event_reaches_the_detailed_reader(the_estate, OWNER, service, landing)
