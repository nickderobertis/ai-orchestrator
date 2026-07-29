"""Unit tests for the repo-plan run ledger and guided continuation."""

from __future__ import annotations

import json
import os
import signal
import socket

import pytest

from orchestrator.config import ConfigError
from orchestrator.next_round import main, main_runs
from orchestrator.runs import (
    AbandonedLaunch,
    AbandonedRound,
    abandoned_launch,
    abandoned_launch_indicator,
    abandoned_round,
    abandoned_round_indicator,
    as_result_payload,
    latest_round,
    list_runs,
    load_completions,
    prepare_round,
    record_completions,
    resolve_run_dir,
    result_state,
    round_abandonment_guard,
    round_appears_in_flight,
    status_summary,
    validate_run_id,
    write_next_plan,
    write_result,
)

PLAN = {"name": "Useful run", "concurrency": 1, "tasks": [{"id": "a"}]}


def _result(status: str) -> dict:
    return {
        "ok": status == "done",
        "started_order": ["a"],
        "results": {"a": {"status": status}},
    }


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "a\\b", "..", "/absolute"])
def test_run_id_rejects_paths(run_id: str) -> None:
    with pytest.raises(ConfigError, match="run id"):
        validate_run_id(run_id)


def test_list_runs_empty_and_invalid_result_payload(tmp_path) -> None:
    assert list_runs(tmp_path / "missing") == []
    with pytest.raises(ConfigError, match="invalid tracked-graph"):
        as_result_payload({"ok": True, "started_order": [], "results": {"a": {}}})


@pytest.mark.parametrize(
    "human_actions",
    [
        {"ref": "h", "task": "Review", "unblocks": [], "unblocks_publication": False},
        [{"ref": "h", "task": "Review", "unblocks": ["a"], "unblocks_publication": "no"}],
        [{"ref": "h", "task": "Review", "unblocks": [1], "unblocks_publication": False}],
        [{"ref": "h", "unblocks": [], "unblocks_publication": False}],
    ],
)
def test_result_payload_rejects_malformed_human_actions(human_actions) -> None:
    payload = {
        "ok": False,
        "state": "waiting",
        "started_order": ["h"],
        "results": {"h": {"status": "waiting", "human_actions": human_actions}},
    }

    with pytest.raises(ConfigError, match="invalid human_actions"):
        as_result_payload(payload)
    with pytest.raises(ConfigError, match="invalid human_actions"):
        status_summary(payload)


def test_result_state_derives_old_payload_states() -> None:
    assert result_state(_result("failed")) == "failed"
    assert (
        result_state(
            {
                "ok": False,
                "started_order": ["h"],
                "results": {"h": {"status": "waiting"}},
            }
        )
        == "waiting"
    )
    assert result_state(_result("done")) == "complete"


def test_resolve_implicit_run_is_fresh(tmp_path) -> None:
    first = resolve_run_dir(tmp_path, PLAN, tmp_path / "fallback.json", None)
    assert first.name == "Useful-run"
    first.mkdir()
    second = resolve_run_dir(tmp_path, PLAN, tmp_path / "fallback.json", None)
    assert second.name.startswith("Useful-run-") and second != first


def test_round_numbering_latest_and_pending_reuse(tmp_path) -> None:
    run_dir = tmp_path / "run"
    number, first = write_next_plan(run_dir, PLAN)
    assert number == 1 and latest_round(run_dir) == (1, first)
    assert prepare_round(run_dir, PLAN) == (1, first)
    write_result(first, _result("failed"))
    number, second = prepare_round(run_dir, PLAN)
    assert number == 2 and second.name == "round-02"
    with pytest.raises(ConfigError, match="pending different"):
        prepare_round(run_dir, {"tasks": []})


def test_write_result_rejects_duplicate_result(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = write_next_plan(run_dir, PLAN)
    write_result(round_dir, _result("done"))
    with pytest.raises(ConfigError, match="already has a result"):
        write_result(round_dir, _result("done"))


def test_recovery_refuses_live_owner_and_claims_abandoned_round(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    with pytest.raises(ConfigError, match="owner may still be alive; recovery refused"):
        prepare_round(run_dir, PLAN, recover=True)

    status = round_dir / "status.json"
    status.write_text(
        json.dumps(
            {"status": "running", "pid": os.getpid() + 10_000_000, "host": socket.gethostname()}
        ),
        encoding="utf-8",
    )
    assert prepare_round(run_dir, PLAN, recover=True) == (1, round_dir)
    recovered = json.loads(status.read_text(encoding="utf-8"))
    assert recovered["pid"] == os.getpid()


@pytest.mark.parametrize(
    "owner",
    [
        {"status": "running", "pid": "not-a-pid", "host": "host"},
        {"status": "completed", "pid": 123, "host": "host"},
    ],
)
def test_recovery_refuses_invalid_owner_metadata(tmp_path, owner) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    (round_dir / "status.json").write_text(json.dumps(owner), encoding="utf-8")

    with pytest.raises(ConfigError, match="invalid owner metadata; recovery refused"):
        prepare_round(run_dir, PLAN, recover=True)


def _own(round_dir, **overrides) -> None:
    """Rewrite a claimed round's owner record."""
    record = {"status": "running", "pid": os.getpid(), "host": socket.gethostname(), **overrides}
    (round_dir / "status.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_signalled_round_records_its_abandonment_and_stops_being_live(tmp_path) -> None:
    """The real signalled journey is tests/e2e/test_round_ownership_e2e.py.

    Here the installed handler is invoked directly, with the signal blocked so its
    final self-signal stays pending; setting the disposition to `SIG_IGN` before
    unblocking discards that pending signal so the test process survives what a round
    owner would not.
    """
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    with round_abandonment_guard(round_dir):
        installed = signal.getsignal(signal.SIGTERM)
        assert callable(installed)
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            installed(signal.SIGTERM, None)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        finally:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})

    recorded = json.loads((round_dir / "status.json").read_text(encoding="utf-8"))
    assert recorded["status"] == "abandoned"
    assert recorded["reason"] == f"owner pid {os.getpid()} took SIGTERM"
    assert recorded["pid"] == os.getpid()
    assert round_appears_in_flight(round_dir) is False
    assert abandoned_round(run_dir) == AbandonedRound(1, os.getpid(), recorded["reason"])
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL


def test_leaving_a_claimed_round_without_a_result_records_the_abandonment(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    with pytest.raises(RuntimeError, match="round exploded"), round_abandonment_guard(round_dir):
        raise RuntimeError("round exploded")

    recorded = json.loads((round_dir / "status.json").read_text(encoding="utf-8"))
    assert recorded["status"] == "abandoned"
    assert recorded["reason"] == f"owner pid {os.getpid()} stopped without recording a result"


def test_a_recorded_result_leaves_the_guard_with_nothing_to_abandon(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    with round_abandonment_guard(round_dir):
        write_result(round_dir, _result("done"))

    assert json.loads((round_dir / "status.json").read_text(encoding="utf-8"))["status"] == (
        "completed"
    )
    assert abandoned_round(run_dir) is None


def test_an_abandoned_round_is_reclaimed_only_with_recover(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    gone = os.getpid() + 10_000_000
    _own(round_dir, status="abandoned", pid=gone, reason=f"owner pid {gone} took SIGTERM")

    with pytest.raises(ConfigError, match="was abandoned.*reclaim it with --recover"):
        prepare_round(run_dir, PLAN)
    assert prepare_round(run_dir, PLAN, recover=True) == (1, round_dir)
    assert json.loads((round_dir / "status.json").read_text(encoding="utf-8")) == {
        **json.loads((round_dir / "status.json").read_text(encoding="utf-8")),
        "status": "running",
        "pid": os.getpid(),
    }


def test_an_abandoned_round_naming_no_usable_owner_is_still_reclaimable(tmp_path) -> None:
    """`--recover` must still reach the one round it exists for.

    A `running` record naming no usable owner is a contradiction and is refused, but an
    abandoned round is already over. Refusing it for an owner there is nothing left to
    probe would leave the operator no way to finish the run.
    """
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    _own(round_dir, status="abandoned", pid="unreadable")

    assert prepare_round(run_dir, PLAN, recover=True) == (1, round_dir)


@pytest.mark.parametrize(
    ("owner", "in_flight"),
    [
        ({"pid": os.getpid()}, True),
        ({"pid": os.getpid() + 10_000_000}, False),
        ({"pid": "not-a-pid"}, True),
        ({"pid": True}, True),
        ({"pid": 0}, True),
        ({"host": "some-other-host"}, True),
        ({"status": "completed"}, False),
    ],
)
def test_a_round_reads_as_in_flight_unless_its_owner_is_proven_gone(
    tmp_path, owner, in_flight
) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    _own(round_dir, **owner)
    assert round_appears_in_flight(round_dir) is in_flight


def test_a_round_with_no_readable_owner_is_never_called_abandoned(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    (round_dir / "status.json").unlink()
    assert round_appears_in_flight(round_dir) is False
    assert abandoned_round(run_dir) is None

    (round_dir / "status.json").write_text("{ not json", encoding="utf-8")
    assert round_appears_in_flight(round_dir) is True
    assert abandoned_round(run_dir) is None

    _own(round_dir, status="completed")
    assert abandoned_round(run_dir) is None
    assert abandoned_round(tmp_path / "never-run") is None


def test_an_abandoned_round_names_its_owner_and_how_to_reclaim_it(tmp_path) -> None:
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    _own(round_dir, pid=os.getpid() + 10_000_000)
    assert abandoned_round_indicator(run_dir) == (
        f"round-01 ABANDONED (owner pid {os.getpid() + 10_000_000} is gone); reclaim with: "
        f"just run-plan {round_dir / 'plan.json'} --run run --runs-dir {tmp_path} --recover"
    )

    _own(round_dir, status="abandoned", pid="unreadable", reason="  ")
    assert abandoned_round(run_dir) == AbandonedRound(1, None, None)
    assert "(recorded owner is gone)" in str(abandoned_round_indicator(run_dir))
    assert abandoned_round_indicator(tmp_path / "never-run") is None


@pytest.mark.parametrize(
    "reason",
    [
        "\x1b[2J\x1b]0;owned\x07",
        "took SIGTERM\nrun round-01 completed successfully",
        "x" * 201,
        42,
    ],
    ids=["escape-sequence", "smuggled-line", "unbounded", "not-a-string"],
)
def test_a_recorded_reason_unfit_for_a_terminal_never_reaches_one(tmp_path, reason) -> None:
    """`status.json` is externally writable, so its reason is an untrusted input.

    It is printed verbatim beside the command that reclaims the round, so a reason the
    terminal would act on rather than show — or one long enough to bury that command —
    has to be dropped for the reason derived from the recorded owner.
    """
    run_dir = tmp_path / "run"
    _, round_dir = prepare_round(run_dir, PLAN)
    dead = os.getpid() + 10_000_000
    _own(round_dir, status="abandoned", pid=dead, reason=reason)

    assert abandoned_round(run_dir) == AbandonedRound(1, dead, None)
    assert abandoned_round_indicator(run_dir) == (
        f"round-01 ABANDONED (owner pid {dead} is gone); reclaim with: "
        f"just run-plan {round_dir / 'plan.json'} --run run --runs-dir {tmp_path} --recover"
    )


def _orchestrator_launch(run_dir, **overrides) -> None:
    """Record a launched orchestrator the way `just orchestrate` does."""
    (run_dir / "orchestrator").mkdir(parents=True, exist_ok=True)
    (run_dir / "launch.json").write_text(json.dumps({"run_id": run_dir.name}), encoding="utf-8")
    record = {"status": "running", "pid": os.getpid(), "host": socket.gethostname(), **overrides}
    (run_dir / "orchestrator" / "status.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_launch_whose_orchestrator_is_gone_is_reported_as_settled(tmp_path) -> None:
    """The whole symptom: a run nothing is driving reads exactly like a healthy one.

    The real journey — a real `just orchestrate` process killed, and both views read
    back — is tests/e2e/test_orchestrate_launch_e2e.py. This drives the decision
    directly so its rendering counts toward the coverage gate.
    """
    run_dir = tmp_path / "stranded"
    _, round_dir = prepare_round(run_dir, PLAN)
    write_result(round_dir, _result("waiting"))
    dead = os.getpid() + 10_000_000
    _orchestrator_launch(run_dir, pid=dead)

    assert abandoned_launch(run_dir) == AbandonedLaunch(dead, 1)
    assert abandoned_launch_indicator(run_dir) == (
        f"SETTLED (orchestrator pid {dead} is gone after round-01); nothing is driving "
        f"this run. Review it with: just results stranded --runs-dir {tmp_path}"
    )


def test_a_launch_that_died_before_its_first_round_says_so(tmp_path) -> None:
    run_dir = tmp_path / "early"
    run_dir.mkdir()
    _orchestrator_launch(run_dir, pid=os.getpid() + 10_000_000)

    assert abandoned_launch(run_dir) == AbandonedLaunch(os.getpid() + 10_000_000, None)
    assert "gone before its first round" in str(abandoned_launch_indicator(run_dir))


@pytest.mark.parametrize(
    "prepare",
    [
        pytest.param(lambda run_dir: None, id="no-launch-record"),
        pytest.param(lambda run_dir: _orchestrator_launch(run_dir), id="owner-is-alive"),
        pytest.param(
            lambda run_dir: _orchestrator_launch(run_dir, host="somewhere-else", pid=1),
            id="another-host",
        ),
        pytest.param(
            lambda run_dir: _orchestrator_launch(run_dir, status="completed"), id="already-settled"
        ),
        pytest.param(
            lambda run_dir: _orchestrator_launch(run_dir, pid="not-a-pid"), id="unusable-owner"
        ),
        pytest.param(
            lambda run_dir: (run_dir / "orchestrator").mkdir(parents=True), id="never-recorded"
        ),
    ],
)
def test_a_launch_this_host_cannot_prove_dead_is_left_alone(tmp_path, prepare) -> None:
    """Every unknown resolves toward "still working"; a wrong "dead" is the costly one."""
    run_dir = tmp_path / "unproven"
    run_dir.mkdir()
    prepare(run_dir)

    assert abandoned_launch(run_dir) is None
    assert abandoned_launch_indicator(run_dir) is None


def test_a_launch_is_silent_while_its_own_round_is_still_reported(tmp_path) -> None:
    """One dead run, one line: the round's report names the command that reclaims it."""
    run_dir = tmp_path / "both"
    _, round_dir = prepare_round(run_dir, PLAN)
    dead = os.getpid() + 10_000_000
    _orchestrator_launch(run_dir, pid=dead)

    # A round claimed by a live owner is work in flight, whatever became of the launch.
    assert abandoned_launch(run_dir) is None

    _own(round_dir, pid=dead)

    assert abandoned_round(run_dir) == AbandonedRound(1, dead, None)
    assert abandoned_launch(run_dir) is None


def test_a_launch_that_reported_its_own_outcome_is_not_abandoned(tmp_path) -> None:
    run_dir = tmp_path / "reported"
    run_dir.mkdir()
    _orchestrator_launch(run_dir, pid=os.getpid() + 10_000_000)
    (run_dir / "orchestrator" / "report.json").write_text('{"ok": true}', encoding="utf-8")

    assert abandoned_launch(run_dir) is None


def test_an_unreadable_launch_record_keeps_the_run_silent(tmp_path) -> None:
    run_dir = tmp_path / "unreadable"
    _orchestrator_launch(run_dir)
    (run_dir / "orchestrator" / "status.json").write_text("{ not json", encoding="utf-8")

    assert abandoned_launch(run_dir) is None


def test_runs_cli_reports_an_abandoned_round_beside_a_recorded_one(tmp_path, capsys) -> None:
    """Both listing shapes at once; the real journey is the e2e this mirrors.

    ``tests/e2e/test_round_ownership_e2e.py`` kills real executors for each shape —
    ``test_signalled_executor_records_its_own_abandonment`` for a run with no settled
    history and ``test_runs_reports_a_dead_round_under_the_summary_of_the_last_settled_one``
    for one that already has a ledger row. This direct call exists so the rendering
    counts toward the coverage gate, which a subprocess CLI invocation cannot.
    """
    dead = tmp_path / "dead"
    _, first = write_next_plan(dead, PLAN)
    write_result(first, _result("done"))
    _, second = write_next_plan(dead, PLAN)
    _own(second, pid=os.getpid() + 10_000_000)
    unrecorded = tmp_path / "unrecorded"
    _, only = write_next_plan(unrecorded, PLAN)
    _own(only, status="abandoned", reason="owner pid 99 took SIGHUP")

    assert main_runs(["--runs-dir", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "! unrecorded  round-01 ABANDONED (owner pid 99 took SIGHUP)" in out
    assert "! dead  round-01  (1 done)" in out
    assert f"    round-02 ABANDONED (owner pid {os.getpid() + 10_000_000} is gone)" in out
    assert "No recorded runs" not in out


def test_list_runs_uses_latest_completed_round(tmp_path) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    write_result(round_dir, _result("done"))
    assert list_runs(tmp_path) == [("demo", 1, "1 done")]


def test_list_runs_surfaces_follow_ups(tmp_path) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    result = _result("done")
    result["results"]["a"]["follow_ups"] = "- Add the adjacent regression test."
    write_result(round_dir, result)
    assert list_runs(tmp_path) == [
        (
            "demo",
            1,
            "1 done; follow-ups: a: - Add the adjacent regression test.",
        )
    ]


def test_status_summary_surfaces_waiting_human_action(tmp_path) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "status": "waiting",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review the result",
                            "unblocks": ["after"],
                            "unblocks_publication": False,
                        }
                    ],
                }
            },
        },
    )

    assert list_runs(tmp_path) == [
        ("demo", 1, "1 waiting; awaiting h: Review the result -> unblocks after")
    ]


def test_status_summary_waiting_downstream_variants() -> None:
    payload = {
        "ok": False,
        "state": "waiting",
        "started_order": ["a", "b"],
        "results": {
            "a": {
                "status": "waiting",
                "human_actions": [
                    {
                        "ref": "a",
                        "task": "Publish?",
                        "unblocks": [],
                        "unblocks_publication": True,
                    }
                ],
            },
            "b": {
                "status": "waiting",
                "human_actions": [
                    {
                        "ref": "b",
                        "task": "",
                        "unblocks": [],
                        "unblocks_publication": False,
                    }
                ],
            },
        },
    }

    summary = status_summary(payload)
    assert "unblocks workstream publication" in summary
    assert "unblocks nothing downstream" in summary


def test_human_completion_ledger_records_and_rejects_duplicates(tmp_path) -> None:
    run = tmp_path / "demo"
    run.mkdir()
    record_completions(run, ["h"], round_number=2)
    assert load_completions(run)[0]["ref"] == "h"
    with pytest.raises(ConfigError, match="already completed"):
        record_completions(run, ["h"], round_number=2)


def test_human_completion_ledger_rejects_bad_shape_and_duplicate_input(tmp_path) -> None:
    run = tmp_path / "demo"
    run.mkdir()
    with pytest.raises(ConfigError, match="unique"):
        record_completions(run, ["h", "h"], round_number=1)
    (run / "humans.json").write_text(json.dumps({"completions": [{"ref": "h"}]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid human-completion"):
        load_completions(run)


def test_next_round_noop_does_not_create_round(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, PLAN)
    write_result(round_dir, _result("done"))
    rc = main(["demo", "--runs-dir", str(tmp_path)])
    assert rc == 0 and latest_round(run) == (1, round_dir)
    assert "nothing to iterate" in capsys.readouterr().out


def test_next_round_rejects_missing_run_and_pending_result(tmp_path, capsys) -> None:
    assert main(["missing", "--runs-dir", str(tmp_path)]) == 2
    assert "no recorded rounds" in capsys.readouterr().err

    run = tmp_path / "demo"
    write_next_plan(run, PLAN)
    assert main(["demo", "--runs-dir", str(tmp_path)]) == 2
    assert "latest round has no result" in capsys.readouterr().err


def test_plan_only_and_runs_cli(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    plan = {"tasks": [{"id": "a", "repo": "x", "persona": "p", "task": "t"}]}
    _, round_dir = write_next_plan(run, plan)
    write_result(round_dir, _result("failed"))
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"retry": {"a": {"max_turns": 8}}}), encoding="utf-8")
    assert main(["demo", str(edits), "--runs-dir", str(tmp_path), "--plan-only"]) == 0
    assert json.loads((run / "round-02" / "plan.json").read_text())["tasks"][0]["max_turns"] == 8
    assert main_runs(["--runs-dir", str(tmp_path)]) == 0
    assert "demo  round-01" in capsys.readouterr().out


def test_runs_cli_no_recorded_runs(tmp_path, capsys) -> None:
    assert main_runs(["--runs-dir", str(tmp_path)]) == 0
    assert "No recorded runs" in capsys.readouterr().out


def _launch(runs_dir, run_id: str, *, pending: object = None):
    """Record a live orchestrator launch the way `just orchestrate` leaves one."""
    run = runs_dir / run_id
    channel = run / "channel"
    channel.mkdir(parents=True)
    (run / "launch.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    (run / "orchestrator").mkdir()
    (run / "orchestrator" / "status.json").write_text(
        json.dumps({"status": "running", "pid": os.getpid(), "host": socket.gethostname()}),
        encoding="utf-8",
    )
    if pending is not None:
        (channel / "planner-pending.json").write_text(
            pending if isinstance(pending, str) else json.dumps(pending), encoding="utf-8"
        )
    return run


def test_runs_cli_marks_active_launches_and_what_they_wait_on(tmp_path, capsys) -> None:
    """`just runs` must show a live orchestrator, including one with no round yet."""
    _launch(
        tmp_path,
        "unrecorded",
        pending={"kind": "blocker", "message": "gate is red", "blocking": True},
    )
    recorded = _launch(
        tmp_path,
        "recorded",
        pending={"kind": "milestone", "message": "round 1 settled", "blocking": False},
    )
    _, round_dir = write_next_plan(recorded, PLAN)
    write_result(round_dir, _result("done"))
    quiet = _launch(tmp_path, "quiet")
    _, quiet_round = write_next_plan(quiet, PLAN)
    write_result(quiet_round, _result("done"))

    assert main_runs(["--runs-dir", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "* unrecorded  ACTIVE  (waiting for planner decision: blocker: gate is red)" in out
    assert "* recorded  round-01  (waiting for planner reply: milestone: round 1 settled)" in out
    assert "* quiet  round-01  (1 done)" in out
    assert "No recorded runs" not in out


def test_runs_cli_falls_back_when_a_live_channel_cannot_be_read(tmp_path, capsys) -> None:
    """An unreadable planner surface must not hide the run from the ledger view."""
    _launch(tmp_path, "unrecorded", pending="{ not json")
    recorded = _launch(tmp_path, "recorded", pending={"kind": "nonsense", "message": "x"})
    _, round_dir = write_next_plan(recorded, PLAN)
    write_result(round_dir, _result("done"))

    assert main_runs(["--runs-dir", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "* unrecorded  ACTIVE  (orchestrator running)" in out
    assert "* recorded  round-01  (1 done)" in out


def test_runs_cli_ignores_a_launch_whose_orchestrator_exited(tmp_path, capsys) -> None:
    """A finished launch loses its active marker even before its rounds are pruned."""
    finished = _launch(tmp_path, "finished", pending={"kind": "closeout", "message": "done"})
    (finished / "orchestrator" / "report.json").write_text("{}", encoding="utf-8")
    _, round_dir = write_next_plan(finished, PLAN)
    write_result(round_dir, _result("done"))

    assert main_runs(["--runs-dir", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "  finished  round-01  (1 done)" in out
    assert "ACTIVE" not in out


def test_next_round_complete_human_records_attestation_and_releases_dep(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    plan = {
        "tasks": [
            {"id": "h", "kind": "human", "task": "Review"},
            {"id": "after", "persona": "engineer", "task": "After", "deps": ["h"]},
        ]
    }
    _, round_dir = write_next_plan(run, plan)
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "kind": "human",
                    "status": "waiting",
                    "task": "Review",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review",
                            "unblocks": ["after"],
                            "unblocks_publication": False,
                        }
                    ],
                },
                "after": {"status": "blocked", "blocked_by": ["h"]},
            },
        },
    )

    rc = main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "h", "--plan-only"])

    assert rc == 0
    assert load_completions(run)[0]["ref"] == "h"
    next_plan = json.loads((run / "round-02" / "plan.json").read_text(encoding="utf-8"))
    assert next_plan["tasks"] == [
        {"id": "after", "persona": "engineer", "task": "After", "deps": []}
    ]
    assert "just run-plan" in capsys.readouterr().out


def test_terminal_human_attestation_records_completed_run_state(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    plan = {"tasks": [{"id": "h", "kind": "human", "task": "Give final approval"}]}
    _, round_dir = write_next_plan(run, plan)
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "kind": "human",
                    "status": "waiting",
                    "task": "Give final approval",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Give final approval",
                            "unblocks": [],
                            "unblocks_publication": False,
                        }
                    ],
                }
            },
        },
    )

    assert main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "h"]) == 0

    second = run / "round-02"
    assert json.loads((second / "plan.json").read_text(encoding="utf-8"))["tasks"] == []
    result = json.loads((second / "result.json").read_text(encoding="utf-8"))
    assert result == {
        "ok": True,
        "state": "complete",
        "started_order": [],
        "results": {
            "h": {
                "kind": "human",
                "status": "done",
                "task": "Give final approval",
                "error": None,
            }
        },
    }
    assert list_runs(tmp_path) == [("demo", 2, "1 done")]
    assert "Round 02 recorded" in capsys.readouterr().err


def test_next_round_rejects_invalid_human_completion_refs(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, {"tasks": [{"id": "h", "kind": "human", "task": "Review"}]})
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "status": "waiting",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review",
                            "unblocks": [],
                            "unblocks_publication": False,
                        }
                    ],
                }
            },
        },
    )
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"complete_human": "h"}), encoding="utf-8")

    assert main(["demo", str(edits), "--runs-dir", str(tmp_path)]) == 2
    assert "complete_human" in capsys.readouterr().err

    assert main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "agent"]) == 2
    assert "recorded waiting human" in capsys.readouterr().err

    assert (
        main(
            [
                "demo",
                "--runs-dir",
                str(tmp_path),
                "--complete-human",
                "h",
                "--complete-human",
                "h",
            ]
        )
        == 2
    )
    assert "unique" in capsys.readouterr().err


def test_next_round_rejects_already_completed_human_ref(tmp_path, capsys) -> None:
    run = tmp_path / "demo"
    _, round_dir = write_next_plan(run, {"tasks": [{"id": "h", "kind": "human", "task": "Review"}]})
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "status": "waiting",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review",
                            "unblocks": [],
                            "unblocks_publication": False,
                        }
                    ],
                }
            },
        },
    )
    record_completions(run, ["h"], round_number=1)

    assert main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "h"]) == 2
    assert "already completed" in capsys.readouterr().err


def test_next_round_reports_completion_record_failure(tmp_path, monkeypatch, capsys) -> None:
    run = tmp_path / "demo"
    plan = {
        "tasks": [
            {"id": "h", "kind": "human", "task": "Review"},
            {"id": "after", "persona": "engineer", "task": "After", "deps": ["h"]},
        ]
    }
    _, round_dir = write_next_plan(run, plan)
    write_result(
        round_dir,
        {
            "ok": False,
            "state": "waiting",
            "started_order": ["h"],
            "results": {
                "h": {
                    "status": "waiting",
                    "human_actions": [
                        {
                            "ref": "h",
                            "task": "Review",
                            "unblocks": ["after"],
                            "unblocks_publication": False,
                        }
                    ],
                },
                "after": {"status": "blocked", "blocked_by": ["h"]},
            },
        },
    )

    def fail_record(run_dir, refs, *, round_number):
        raise ConfigError("disk full")

    monkeypatch.setattr("orchestrator.next_round.record_completions", fail_record)
    assert main(["demo", "--runs-dir", str(tmp_path), "--complete-human", "h"]) == 2
    assert "disk full" in capsys.readouterr().err
