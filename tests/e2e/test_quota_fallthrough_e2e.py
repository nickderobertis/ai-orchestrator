"""The zero-work subscription rejection the agent side's fallback chain exists for.

A Claude subscription that is out of session quota does not say so plainly. It
answers with a terminal record that reads as a success — `subtype: "success"`, no
`is_error`, an empty answer — and declares the rejection only through
`terminal_reason: "api_error"` and an embedded `api_error_status` of `429`. Nothing
was spent: no tokens, no cost, no `modelUsage`.

The adopted oneharness must read that as *this candidate could not run the task at
all* and hand the turn to the next identity in `oneharness.toml`. Under the previous
pin it read as a transient `rate_limit` and stopped the chain, which cost a tracked
run all three of its round-01 nodes ("did not complete after 1 turn") and left
`--worker-harness` as the hand-routed workaround. These journeys are the contract
that retires it, and its boundary: a rejection carrying *work* still stops the
chain, because falling through one that already spent tokens would buy the same
work twice.

A real 429 is an external event no dispatch controls, so the proof is the record's
shape driven through the real `oneharness.toml` chain and the real CLI, invoked the way
the engine invokes it for a dispatch's turn: `oneharness run --config oneharness.toml
--format json --compact --events`.
"""

# llmlint: ignore-file[e2e_not_mocked] the repository requires faking the paid agent
# harness and does it here at its designated seam (oneharness's own shipped mock
# responder for the rejecting candidate, `tests/e2e/fake_codex.py` for the one that
# takes over); the agent config, the fallback chain, the classifier and the report are
# all real.
# llmlint: ignore-file[shell_test_tiers_stay_split] Nothing here is expensive: both
# providers are doubled, the whole module ran in 0.64s with its slowest call at 0.19s
# under `pytest --durations`, and the real `oneharness` CLI it drives is the one
# tests/e2e/test_oneharness_timeout_e2e.py and tests/e2e/test_claude_identity_routing_e2e.py
# already drive over the same configs in this tier, so a project of its own would
# isolate no cost.

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast, get_args

from mock_oneharness import main as _mock_oneharness_main
from waits import timeout

from orchestrator.root import REPO_ROOT

MOCK_ONEHARNESS = Path(_mock_oneharness_main.__globals__["__file__"]).resolve()
#: The candidate that takes the turn over: a real codex-shaped stream, this
#: repository's one faked boundary, which reports token accounting of its own so a
#: fall-through can be told from a chain that simply reported nothing.
FAKE_CODEX = REPO_ROOT / "tests" / "e2e" / "fake_codex.py"
#: The answer `fake_codex.py` returns, and therefore the evidence that the second
#: candidate really ran rather than the first one's empty result being reported.
FALLBACK_ANSWER = "smoke-ok"


#: The closed vocabulary oneharness publishes a work reading in.
Work = Literal["done", "none"]
#: The history schema version a record declares once it carries one. A record with
#: no reading to carry declares less, which is what keeps it legible to a reader
#: that has never heard of the field — asserted as the relation below as well, so a
#: renumbered contract fails here rather than silently becoming untested.
WORK_EVIDENCE_SCHEMA_VERSION = "1.7"


class Accounting(TypedDict):
    """What a Claude terminal record says the turn spent."""

    input_tokens: int
    output_tokens: int


class ModelAccounting(TypedDict):
    """One model's share of that spend.

    camelCase because these are the wire names Claude Code writes, and the map being
    non-empty is one of the three things the classifier reads as work done — a
    record spelled our way would not be the record it has to classify.
    """

    inputTokens: int
    outputTokens: int


class Rejection(TypedDict):
    """A Claude Code terminal record that refuses the turn without saying so.

    The classification rests on the accounting rather than on any of the prose, so
    these fields are the whole input: what the harness claims happened, and what it
    charged for.
    """

    type: str
    subtype: str
    terminal_reason: str
    api_error_status: int
    result: str
    usage: Accounting
    total_cost_usd: float
    modelUsage: dict[str, ModelAccounting]


#: A subscription limit rejected before any of the task's work: the accounting is
#: the discriminator, so every counter it carries is zero and `modelUsage` is empty.
ZERO_WORK_REJECTION: Rejection = {
    "type": "result",
    "subtype": "success",
    "terminal_reason": "api_error",
    "api_error_status": 429,
    "result": "",
    "usage": {"input_tokens": 0, "output_tokens": 0},
    "total_cost_usd": 0.0,
    "modelUsage": {},
}


class Answer(TypedDict):
    """A Claude Code terminal record for a turn that ran and answered."""

    type: str
    subtype: str
    result: str
    usage: Accounting
    total_cost_usd: float
    modelUsage: dict[str, ModelAccounting]


#: What the identity *behind* a refusing one answers if the chain ever reaches it.
#: A success rather than a second refusal, so a chain that wrongly moved past a
#: candidate is a visible answer in the report rather than another stop that looks
#: like the one being asserted on.
UNREACHED_ANSWER: Answer = {
    "type": "result",
    "subtype": "success",
    "result": "second-identity-ran",
    "usage": {"input_tokens": 900, "output_tokens": 120},
    "total_cost_usd": 0.04,
    "modelUsage": {"claude-sonnet-5": {"inputTokens": 900, "outputTokens": 120}},
}
#: The identical rejection from a turn that had already been paid for. This is the
#: boundary the fall-through must not cross: the record says exactly the same thing
#: about the failure and something entirely different about what it cost.
WORKED_REJECTION: Rejection = {
    **ZERO_WORK_REJECTION,
    "usage": {"input_tokens": 1200, "output_tokens": 340},
    "total_cost_usd": 0.21,
    "modelUsage": {"claude-opus-5-5": {"inputTokens": 1200, "outputTokens": 340}},
}


@dataclass(frozen=True)
class Candidate:
    """One attempted candidate's verdict, as this journey reads it."""

    status: str
    failure_kind: str | None
    text: str | None
    output_tokens: int
    #: What this candidate has to show for itself when nothing classified its
    #: failure: `"none"` when the harness recorded no tool call and no billed
    #: usage, `"done"` when it did, and `None` on every other outcome. oneharness
    #: publishes it only for an unclassified failure, which is the one reading a
    #: consumer cannot derive. The vocabulary is closed, so a third token is a
    #: contract change this journey should refuse rather than carry.
    work: Work | None


@dataclass(frozen=True)
class Persisted:
    """One `type: "run"` line the harness wrote to its own history store.

    The report a caller reads and the record a *later* reader reads are two
    surfaces, and only the second one has to stay legible to a reader older than
    the writer — which is this host's situation, since the `oneagentgraph` that
    judges the smoke links a `oneharness-core` behind the CLI it spawns.
    """

    schema_version: str
    work: Work | None


@dataclass(frozen=True)
class Turn:
    """One agent-side turn: how it exited, and the single report it printed."""

    exit_code: int
    stdout: str
    stderr: str
    #: The candidate that executed, or `None` when none of them could.
    ran: str | None
    #: Every candidate the chain moved past, in priority order, with its reason.
    fell_through: tuple[tuple[str, str], ...]
    #: The candidates actually attempted, by harness id. A candidate the chain
    #: never reached is absent — which is how "its quota was never touched" is read.
    attempted: Mapping[str, Candidate]
    #: Whether the chain stopped at a candidate that showed nothing for itself.
    stopped_without_work: bool
    #: What each candidate's own history record says, by harness id.
    persisted: Mapping[str, Persisted]


def _read_history(history_dir: Path) -> dict[str, Persisted]:
    """Every run line the harness persisted, by harness id.

    The store also holds an index whose lines wrap a record in an envelope naming
    no harness of its own, so only `type: "run"` lines are read — the same rule the
    smoke's own verdict is read under.
    """
    persisted: dict[str, Persisted] = {}
    for path in sorted(history_dir.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("type") != "run":
                continue
            persisted[record["harness_id"]] = Persisted(
                schema_version=record["schema_version"],
                work=_work(record.get("work")),
            )
    return persisted


def _work(value: object) -> Work | None:
    """The work reading a record carries, refusing one this journey never measured.

    The vocabulary is the harness's, and every assertion below is written against
    the two tokens it publishes today. A third would make those assertions describe
    a contract nobody re-read, so it fails here — at the boundary the value crosses
    — rather than passing through as a string that happens not to match.
    """
    if value is None:
        return None
    assert value in get_args(Work), (
        f"unmeasured work reading {value!r}: the harness publishes a token this "
        "journey has not been re-read against"
    )
    return cast(Work, value)


def _schema(persisted: Persisted) -> tuple[int, ...]:
    """A history record's declared schema version, ordered."""
    return tuple(int(part) for part in persisted.schema_version.split("."))


def _read_turn(completed: subprocess.CompletedProcess[str], history_dir: Path) -> Turn:
    """Model the one report the turn printed, refusing anything that is not one.

    onejudge parses this process's stdout as exactly one report, whichever candidate
    produced it, so a chain that fell through must not have added a line.
    """
    lines = completed.stdout.strip().splitlines()
    assert len(lines) == 1, completed.stdout
    report = json.loads(lines[0])
    fallback = report["fallback"]
    return Turn(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        ran=fallback["ran"],
        fell_through=tuple(
            (entry["harness"], entry["reason"]) for entry in fallback["fell_through"]
        ),
        attempted={
            result["harness_id"]: Candidate(
                status=result["status"],
                failure_kind=result["failure_kind"],
                text=result["text"],
                output_tokens=result["usage"]["output_tokens"],
                work=_work(result["work"]),
            )
            for result in report["results"]
        },
        stopped_without_work=fallback["stopped_without_work"],
        persisted=_read_history(history_dir),
    )


def _chain_turn(tmp_path: Path, oneharness_bin: str, selection: Mapping[str, str]) -> Turn:
    """Run one agent-side turn through the real agent config, chain and classifier.

    `selection` is the only thing a journey varies, and it is an environment: which
    identities the chain names and in what order, which provider binary stands in
    for each, and what that provider then does. Everything else is built from
    nothing here, so no pin the surrounding dispatch exported can reach it.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    oneharness = bin_dir / "oneharness"
    if not oneharness.exists():
        oneharness.symlink_to(MOCK_ONEHARNESS)
    # One store per turn, so a journey that spends two of them reads each one's own
    # records rather than whichever wrote last — and so no journey here touches the
    # store a real turn on this host writes to.
    history_dir = Path(tempfile.mkdtemp(prefix="history-", dir=tmp_path))
    return _read_turn(
        subprocess.run(
            [
                str(oneharness),
                "run",
                "--config",
                str(REPO_ROOT / "oneharness.toml"),
                "--format",
                "json",
                "--compact",
                "--events",
                "--prompt",
                "answer this turn",
            ],
            text=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env={
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "HOME": str(tmp_path / "home"),
                "PYTHONPATH": str(REPO_ROOT / "tests" / "e2e"),
                "REAL_ONEHARNESS_BIN": oneharness_bin,
                "ONEHARNESS_HISTORY_DIR": str(history_dir),
                **selection,
            },
            timeout=timeout(60),
        ),
        history_dir,
    )


def _agent_turn(tmp_path: Path, oneharness_bin: str, rejection: Rejection) -> Turn:
    """Run one agent-side turn whose first candidate answers with `rejection`."""
    return _chain_turn(
        tmp_path,
        oneharness_bin,
        {
            # Narrowed to plain ids so the shipped responder can name the
            # candidate it replaces, exactly as the other real-CLI journeys
            # narrow it. The order, the `fallback` run mode and the classifier
            # all stay the agent config's own.
            "MOCK_HARNESSES": "claude-code",
            "ONEHARNESS_HARNESSES": "claude-code,codex",
            "MOCK_STDOUT": json.dumps(rejection),
            "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
        },
    )


def _codex_first_turn(tmp_path: Path, oneharness_bin: str, provider: Mapping[str, str]) -> Turn:
    """Run a turn whose first candidate is codex, with `provider` steering it.

    The identity behind it is the shipped responder rather than a second stand-in,
    so "the chain never reached it" is read off a candidate that would have
    answered — see `UNREACHED_ANSWER`.
    """
    return _chain_turn(
        tmp_path,
        oneharness_bin,
        {
            "MOCK_HARNESSES": "claude-code",
            "ONEHARNESS_HARNESSES": "codex,claude-code",
            "MOCK_STDOUT": json.dumps(UNREACHED_ANSWER),
            "ONEHARNESS_BIN_CODEX": str(FAKE_CODEX),
            **provider,
        },
    )


def test_a_zero_work_subscription_429_hands_the_turn_to_the_next_identity(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The adopted release classifies the rejection as `quota` and falls through."""
    turn = _agent_turn(tmp_path, oneharness_bin, ZERO_WORK_REJECTION)

    assert turn.exit_code == 0, turn.stderr
    assert turn.ran == "codex"
    assert turn.fell_through == (("claude-code", "quota"),)
    # `quota`, not the transient `rate_limit` that stopped the chain: the record's
    # own accounting says the candidate never got to the task.
    assert turn.attempted["claude-code"].failure_kind == "quota"
    # And the turn has a real answer, from the identity the chain moved on to.
    assert turn.attempted["codex"].status == "ok"
    assert turn.attempted["codex"].text == FALLBACK_ANSWER


def test_the_same_429_after_billed_work_never_reaches_the_next_identity(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Work already paid for is never re-run on the next candidate's quota.

    The fall-through this refuses and the one beside it are the same record apart from
    what the harness billed, so the classifier reading tokens rather than error text is
    the whole guarantee — and a fall-through that widened to cover this case would
    silently double every rejected turn's cost.

    **What the record is called changed under oneharness 0.11.3 and this property did
    not**, which is why the two are asserted apart. Through 0.11.2 the same record was a
    `rate_limit` failure and the turn exited 1; that release reconciled the contradiction
    in it — a completed run, billed, carrying an intermediate rate-limit signal — in
    favour of the completion, so the candidate is `ok` and the turn exits 0
    ([oneharness#1277](https://github.com/nickderobertis/oneharness/pull/1277)). It is the
    repair for two dispatches whose finished work was discarded, about $24.72 and both
    completion reports. The chain still stops here, which is the half this journey is
    named for.
    """
    turn = _agent_turn(tmp_path, oneharness_bin, WORKED_REJECTION)

    assert turn.exit_code == 0, turn.stderr
    assert turn.ran == "claude-code"
    assert turn.fell_through == ()
    assert turn.attempted["claude-code"].status == "ok"
    assert turn.attempted["claude-code"].failure_kind is None
    assert turn.attempted["claude-code"].output_tokens == 340
    # The next identity was never reached, so its quota was never touched. This is the
    # property, and it is the one thing the reclassification above must not have moved:
    # a completed billed turn read as a success that then *fell through* would bill the
    # work twice, which is the outcome both readings exist to prevent.
    assert "codex" not in turn.attempted
    assert FALLBACK_ANSWER not in turn.stdout


def test_a_candidate_that_never_started_stops_the_chain_and_says_so(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The other reason a chain stops, and the one that used to be unreadable.

    A candidate whose provider refused to start leaves a failure no classifier
    recognizes: no `failure_kind`, no tool call, no billed token. The chain stops
    there — deliberately, since a failure it cannot explain is not one it may spend
    the next identity's quota on — but through the previous pin the report said only
    that the candidate `ran but did not succeed`, which is the sentence a genuine
    task failure gets. An operator reading it saw a task failure and the untried
    rest of the chain hidden behind it.

    The adopted release publishes the reading the fall-through verdict already
    consulted: `work` on the candidate, `stopped_without_work` on the chain, and a
    summary sentence that names which of the two stops this was. Nothing about the
    routing moves with it, which is what the untouched second identity here proves.
    """
    turn = _codex_first_turn(
        tmp_path,
        oneharness_bin,
        {
            # The attempt log is what makes the refusal deterministic: the provider
            # counts its own launches and this is the first.
            "FAKE_CODEX_ATTEMPT_LOG": str(tmp_path / "attempts.log"),
            "FAKE_CODEX_UNAVAILABLE_ATTEMPTS": "1",
        },
    )

    assert turn.exit_code == 1
    assert turn.ran == "codex"
    assert turn.fell_through == ()
    # Nothing classified it, so `failure_kind` cannot answer and `work` is what does.
    assert turn.attempted["codex"].failure_kind is None
    assert turn.attempted["codex"].work == "none"
    assert turn.stopped_without_work
    # The chain really did stop: the identity behind it was never asked, so its
    # answer is nowhere in the report.
    assert "claude-code" not in turn.attempted
    assert UNREACHED_ANSWER["result"] not in turn.stdout
    # And the operator is told which of the two stops this was, in the summary the
    # supervisor quotes rather than only in a field it would have to go looking for.
    assert "nothing to show for it" in turn.stderr, turn.stderr
    # The reading survives the turn: a later reader gets it off the record too,
    # rather than only out of the report the caller happened to be holding.
    assert turn.persisted["codex"].work == "none"


def test_the_same_unclassified_failure_with_work_behind_it_reads_as_work_done(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The boundary of the reading above, and the reason it is published at all.

    This candidate fails exactly as unaccountably as the one before it — same
    absent `failure_kind`, same stopped chain, same untried identity behind it —
    and differs only in having answered and been billed first. Two pins ago the two
    were one report; here they are told apart by `work` alone, and by which summary
    sentence oneharness prints. A reading that collapsed them would invite re-running
    work somebody already paid for.

    Which sentence that is moved under the adopted release: through the previous pin
    this stop borrowed `ran but did not succeed`, the sentence a task failure with a
    named cause gets, and oneharness 0.12.1 gives it one of its own — the candidate
    *did the task's work and did not succeed, for a cause it could not classify* —
    beside the status it ended with and what the candidate itself said
    (https://github.com/nickderobertis/oneharness/pull/1286). So a supervisor reading
    the summary is told which of the three stops this was without opening the report.
    """
    turn = _codex_first_turn(
        tmp_path,
        oneharness_bin,
        {
            "FAKE_CODEX_ATTEMPT_LOG": str(tmp_path / "attempts.log"),
            "FAKE_CODEX_FAIL_AFTER_TURN": "1",
        },
    )

    assert turn.exit_code == 1
    assert turn.ran == "codex"
    assert turn.fell_through == ()
    assert turn.attempted["codex"].failure_kind is None
    # The turn was answered and billed, so the failure has work behind it.
    assert turn.attempted["codex"].work == "done"
    assert turn.attempted["codex"].output_tokens == 1
    assert not turn.stopped_without_work
    # Same stop, and the identity behind it is untouched either way.
    assert "claude-code" not in turn.attempted
    assert UNREACHED_ANSWER["result"] not in turn.stdout
    # And the operator gets the sentence that names this stop and no other, with the
    # candidate's own words carried into it rather than left in the report.
    assert "did the task's work and did not succeed, for a cause it could not classify" in (
        turn.stderr
    ), turn.stderr
    assert "fake_codex: the provider answered and then failed" in turn.stderr, turn.stderr
    assert "ran but did not succeed" not in turn.stderr, turn.stderr
    assert turn.persisted["codex"].work == "done"


def test_only_a_record_that_carries_a_work_reading_declares_the_newer_schema(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """The compatibility half of the adoption, and the reason it holds here.

    This host runs a reader older than its writer: the `oneagentgraph` the smoke
    judges by links a `oneharness-core` behind the `oneharness` CLI that writes the
    record. That is safe only because a record declares the version its *contents*
    need — so a turn with nothing new to say stays legible to a reader that has
    never heard of the new field, and only a record actually carrying one asks for
    a newer reader.

    Asserted as the relation rather than as two literals: the numbers are the
    harness's to choose and will move again, while "carrying the reading is what
    costs a version" is the guarantee an older reader depends on.
    """
    without_reading = _codex_first_turn(
        tmp_path,
        oneharness_bin,
        {"FAKE_CODEX_ATTEMPT_LOG": str(tmp_path / "answered.log")},
    )
    with_reading = _codex_first_turn(
        tmp_path,
        oneharness_bin,
        {
            "FAKE_CODEX_ATTEMPT_LOG": str(tmp_path / "failed.log"),
            "FAKE_CODEX_FAIL_AFTER_TURN": "1",
        },
    )

    # The first turn simply succeeded, so there was no unclassified failure to read.
    assert without_reading.exit_code == 0
    assert without_reading.persisted["codex"].work is None
    assert with_reading.persisted["codex"].work == "done"
    assert with_reading.persisted["codex"].schema_version == WORK_EVIDENCE_SCHEMA_VERSION
    assert _schema(with_reading.persisted["codex"]) > _schema(without_reading.persisted["codex"])
