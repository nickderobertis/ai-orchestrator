"""Each remote identity watches its required checks as long as its measured CI runs need.

A publication watches its change request's required checks for the identity's
`checks: {wait_seconds}` before ending `checks-unsettled`, and onevcs waits 3600 s when
a rule names none. `config/onevcs.checks-wait.yml` records, per identity, the CI runs
its wait was sized from and how long each took to its last required check; this journey
holds that record's own arithmetic, then installs the tracked rules with the real `just
repos-apply` into a scratch registry and reads back, through the installed `onevcs rules
check`, the wait a publication of each identity would really use.

llmlint: ignore-file[e2e_not_mocked] The scratch checkouts stand in for this host's own
clones, which a test may not register or publish from; the recipe, the script, `onevcs`,
and git are all real, and the wait asserted is the one `onevcs` resolved.

It runs in the `checks-wait` project of its own, keyed on the rules file, the sizing
record and the registry recipe it drives, so no other change re-runs its real CLIs.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest
import yaml
from registered_checkouts import RepoIdentity
from ruled_registry import TRACKED_RULES, onevcs, reported, ruled_identities, ruled_registry

from orchestrator.root import REPO_ROOT

#: The measurements each change-request identity's `checks: {wait_seconds}` in the rules
#: file is sized from: the CI runs read, at what cutoff, and how long each took.
TRACKED_CHECKS_WAIT = REPO_ROOT / "config" / "onevcs.checks-wait.yml"
#: onevcs's shipped checks wait, which an identity the record does not size past it
#: stays on.
SHIPPED_CHECKS_WAIT = 3600
#: The least margin above an identity's slowest recorded run its wait may carry.
CHECKS_WAIT_MARGIN = 0.25
#: How many of a repository's most recent pull-request CI runs the record samples,
#: across all its CI workflows together: every one when it had fewer.
SAMPLED_RUNS = 10
#: How the record spells its observation cutoff, in UTC.
CUTOFF = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
#: The publication that opens no change request, so watches no required checks.
LOCAL_DIRECT = "local-direct"


class SizedRun(TypedDict):
    """One CI run in `config/onevcs.checks-wait.yml`, with its required checks' timing.

    `unexpanded` is present on a run whose required matrix job ended — skipped or
    cancelled — before its matrix expanded, so the job that stood for the required
    checks is named without their matrix suffix.
    """

    id: int
    workflow: str
    event: str
    attempt: int
    created_at: str
    last_required_completed_at: str
    seconds: int
    last_required_check: str
    unexpanded: NotRequired[str]


class SizedIdentity(TypedDict):
    """What one identity's checks wait was sized from."""

    runs_used: int
    max_seconds: int
    max_run: int
    wait_seconds: int | str
    runs: list[SizedRun]


class ChecksWaitRecord(TypedDict):
    """The whole of `config/onevcs.checks-wait.yml`: how it was read, and per identity."""

    cutoff: str
    default_wait_seconds: int
    margin: float
    sample_size: int
    identities: dict[RepoIdentity, SizedIdentity]


def checks_wait_record() -> dict[RepoIdentity, SizedIdentity]:
    """The sizing record's identities, after checking the record's own arithmetic.

    Every figure is recomputed from the timestamps beside it, so a hand-edited maximum
    or wait that no longer follows from the named runs fails here rather than sizing a
    rule from a number nothing measured.
    """
    record = cast(
        "ChecksWaitRecord", yaml.safe_load(TRACKED_CHECKS_WAIT.read_text(encoding="utf-8"))
    )
    assert record["default_wait_seconds"] == SHIPPED_CHECKS_WAIT
    assert record["margin"] >= CHECKS_WAIT_MARGIN
    assert CUTOFF.fullmatch(record["cutoff"]), record["cutoff"]
    assert record["sample_size"] == SAMPLED_RUNS
    identities = record["identities"]
    for identity, sized in identities.items():
        runs = sized["runs"]
        assert sized["runs_used"] == len(runs) <= SAMPLED_RUNS, identity
        assert len({run["id"] for run in runs}) == len(runs), identity
        created = [run["created_at"] for run in runs]
        assert created == sorted(created, reverse=True), (
            f"{identity}'s sample is not its most recent runs, newest first: {created}"
        )
        assert all(run["created_at"] <= record["cutoff"] for run in runs), identity
        for run in runs:
            assert "seconds" in run and "last_required_completed_at" in run, (
                f"{identity}'s run {run['id']} records no required check's completion"
            )
            elapsed = datetime.fromisoformat(
                run["last_required_completed_at"]
            ) - datetime.fromisoformat(run["created_at"])
            assert run["seconds"] == int(elapsed.total_seconds()), (identity, run)
            assert run["last_required_completed_at"] <= record["cutoff"], (identity, run)
        slowest = max(runs, key=lambda run: run["seconds"])
        assert (sized["max_seconds"], sized["max_run"]) == (slowest["seconds"], slowest["id"])
        needed = sized["max_seconds"] * (1 + record["margin"])
        if needed > SHIPPED_CHECKS_WAIT:
            assert isinstance(sized["wait_seconds"], int), identity
            assert sized["wait_seconds"] >= needed, identity
        else:
            assert sized["wait_seconds"] == "default", identity
    return identities


@pytest.fixture(scope="module")
def ruled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A registry holding a checkout of every identity the tracked rules name."""
    return ruled_registry(tmp_path_factory.mktemp("ruled-registry"))


def test_every_change_request_identity_has_a_checks_wait_sizing(ruled: Path) -> None:
    """The record covers exactly the identities whose resolved publication watches checks."""
    remote: set[RepoIdentity] = set()
    for identity in ruled_identities():
        checked = onevcs(ruled, "rules", "check", identity)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        if reported(checked.stdout, "publication") != LOCAL_DIRECT:
            remote.add(identity)
    assert set(checks_wait_record()) == remote


@pytest.mark.parametrize("key", sorted(frozenset(ruled_identities())))
def test_each_identity_resolves_the_checks_wait_its_measurements_size(
    ruled: Path, monkeypatch: pytest.MonkeyPatch, key: RepoIdentity
) -> None:
    """What a publication would really wait, read off the installed `onevcs`.

    An identity the record sizes past the default resolves its own wait, written with
    the recorded maximum and margin beside it; every other one resolves the default.
    """
    monkeypatch.delenv("ONEVCS_CHECKS_TIMEOUT_SECONDS", raising=False)
    sized = checks_wait_record().get(key)
    wait = sized["wait_seconds"] if sized else "default"
    checked = onevcs(ruled, "rules", "check", key)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    expected = SHIPPED_CHECKS_WAIT if wait == "default" else wait
    assert reported(checked.stdout, "checks wait") == f"{expected} s", checked.stdout
    if sized and wait != "default":
        beside = (
            f"    checks: {{wait_seconds: {wait}}}  # recorded max {sized['max_seconds']} s "
            f"(run {sized['max_run']}) + 25% margin"
        )
        assert beside in TRACKED_RULES.read_text(encoding="utf-8").splitlines(), key
