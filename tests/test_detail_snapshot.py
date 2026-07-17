from __future__ import annotations

import pytest

from orchestrator.detail_snapshot import CommitDetail, PrDetail
from orchestrator.github import Check, PRStatus


@pytest.mark.parametrize(
    "value",
    [None, {}, {"sha": 7}, {"sha": "abc1234", "subject": 7}],
)
def test_commit_detail_rejects_malformed_records(value: object) -> None:
    assert CommitDetail.from_value(value) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"number": True},
        {"number": 0},
        {"number": 1, "state": 7},
        {"number": 1, "merged": "yes"},
        {"number": 1, "draft": "no"},
        {"number": 1, "checks": {}},
        {"number": 1, "revision": True},
        {"number": 1, "revision": -1},
    ],
)
def test_pr_detail_rejects_malformed_records(value: object) -> None:
    assert PrDetail.from_value(value) is None


def test_pr_detail_round_trips_a_typed_status_and_drops_bad_checks() -> None:
    status = PRStatus(
        number=3,
        state="OPEN",
        merged=False,
        merge_state_status="CLEAN",
        checks=(Check("ci", "SUCCESS", True),),
        draft=False,
    )
    detail = PrDetail.from_status(
        status,
        url="https://github.com/acme/app/pull/3",
        identity="acme/app",
        revision=4,
    )
    assert PrDetail.from_value(detail.to_record()) == detail
    assert detail.status() == status

    record = detail.to_record()
    record["checks"] = [*record["checks"], {"name": 7, "state": "SUCCESS", "required": True}]
    assert PrDetail.from_value(record) == detail
