"""The `onemessagebus` command line this checkout installs carries a record end to end.

`config/onemessagebus.version` pins the `onemessagebus-cli` wheel, and a pin is one
number: it says which release `uv sync` put in `.venv` and nothing about whether that
binary moves a message. The observer's judge side and the manager's recipes are pointed
at this binary next, so this journey drives it the way they will — through its own
queue verbs over a transport directory it owns — and holds `onemessagebus --version`
to the pin, the way `tests/e2e/test_linked_engine_reconciliation_e2e.py` drives
`.venv/bin/onepipeline`.

Nothing is doubled: the binary is the installed release, the transport is a local
directory under `tmp_path`, and every reading is the verb's own output. The queue is
`surfaces` under the `planner-channel` layout the binary declares when no
configuration names another, because that is the queue the judge side will raise on.

llmlint: ignore-file[shell_test_tiers_stay_split] This repository runs one Nx project and
splits its tiers by pytest marker over `nx.json` keys, and the binary this drives is the
locked `.venv` install `scripts/nx.sh` heals from `uv.lock` — the input every workspace
tier is already keyed on, exactly as `.venv/bin/onepipeline` is for the journey named
above. It reaches no network and no tool outside the workspace, so a project of its own
would be keyed on the same lock and separate nothing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TypedDict, cast

from orchestrator.root import REPO_ROOT

BUS = REPO_ROOT / ".venv" / "bin" / "onemessagebus"
QUEUE = "surfaces"


class Finding(TypedDict):
    """The fields an observer writes when it raises a finding."""

    kind: str
    message: str
    source: str
    blocking: bool


class Receipt(TypedDict):
    """What `send` answers: where the record went."""

    queue: str
    id: int


class Claim(TypedDict):
    """What `next` answers: the record claimed and the position the claim holds."""

    queue: str
    id: int
    position: int
    record: Finding


class QueueStatus(TypedDict):
    """One queue's entry in `status`, narrowed to what this journey reads.

    `cast` rather than a validating read: `onemessagebus` owns this schema, and every
    field read here is asserted against what the verb was asked to do.
    """

    queue: str
    records: int
    waiting: list[Finding]
    pending: Finding | None
    pending_position: int | None


#: A blocking finding, shaped as an observer raises one.
FINDING = Finding(kind="finding", message="the base moved", source="proposal", blocking=True)


def _bus(transport: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run one verb of the installed binary against ``transport``."""
    return subprocess.run(
        [BUS, *args, "--transport-dir", str(transport)],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def _status(transport: Path) -> QueueStatus:
    """The one queue's entry in `status`, as the verb prints it."""
    reported = _bus(transport, "status", QUEUE)
    assert reported.returncode == 0, reported.stderr
    queues = cast(list[QueueStatus], json.loads(reported.stdout))
    assert [queue["queue"] for queue in queues] == [QUEUE], queues
    return queues[0]


def _as_sent(record: Finding) -> Finding:
    """The fields of ``record`` the sender wrote, without those the queue stamps on it."""
    return Finding(
        kind=record["kind"],
        message=record["message"],
        source=record["source"],
        blocking=record["blocking"],
    )


def test_the_installed_binary_reports_the_pinned_release() -> None:
    adopted = (REPO_ROOT / "config" / "onemessagebus.version").read_text("utf-8").strip()

    reported = subprocess.run([BUS, "--version"], text=True, capture_output=True, check=False)

    assert reported.returncode == 0, reported.stderr
    assert reported.stdout.strip() == f"onemessagebus {adopted}"


def test_a_record_sent_to_a_queue_is_claimed_by_next_and_read_in_status(tmp_path: Path) -> None:
    transport = tmp_path / "channel"

    sent = _bus(transport, "send", QUEUE, stdin=json.dumps(FINDING))

    assert sent.returncode == 0, sent.stderr
    receipt = cast(Receipt, json.loads(sent.stdout))
    assert receipt["queue"] == QUEUE
    waiting = _status(transport)
    assert [_as_sent(record) for record in waiting["waiting"]] == [FINDING]
    assert waiting["pending"] is None

    claimed = _bus(transport, "next", QUEUE)

    assert claimed.returncode == 0, claimed.stderr
    claim = cast(Claim, json.loads(claimed.stdout))
    assert claim["queue"] == QUEUE
    assert claim["id"] == receipt["id"]
    assert _as_sent(claim["record"]) == FINDING

    # A blocking record claimed stays pending until it is answered, at the position the
    # claim was recorded at, and is no longer waiting for a second claimant.
    pending = _status(transport)
    assert pending["waiting"] == []
    assert pending["pending"] == claim["record"]
    assert pending["pending_position"] == claim["position"]


def test_nothing_to_claim_and_a_refused_record_append_nothing(tmp_path: Path) -> None:
    transport = tmp_path / "channel"

    empty = _bus(transport, "next", QUEUE)

    assert empty.returncode == 1
    assert f"nothing on {QUEUE} to claim" in empty.stderr
    assert empty.stdout == ""

    refused = _bus(transport, "send", QUEUE, stdin="[1]")

    assert refused.returncode == 2
    assert "is a JSON object" in refused.stderr
    status = _status(transport)
    assert status["records"] == 0
    assert status["waiting"] == []
    assert status["pending"] is None
