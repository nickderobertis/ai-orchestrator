"""Unit tests for leaving the launching turn before a round claims anything.

The real journey — a `just run-plan` round surviving the teardown of the turn that
launched it — is
`tests/e2e/test_round_ownership_e2e.py::test_a_round_survives_the_teardown_of_its_launching_turn`.
These cover what that journey cannot see from outside the process: that the round runs
in a session of its own, and that the relaying parent reports the round's own outcome
whether it returned an exit code or died under a signal.

The fork is real here too. This test process is the one that forks, and the round it
forks off ends with ``os._exit``, so it never returns into the test — which is exactly
the property that makes calling ``run_detached`` from a test safe.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

from orchestrator.detach import run_detached


def test_detached_round_leads_its_own_session_and_relays_its_exit_code(tmp_path: Path) -> None:
    record = tmp_path / "round.json"

    def entry(argv: list[str] | None) -> int:
        record.write_text(
            json.dumps(
                {
                    "leads_session": os.getsid(0) == os.getpid(),
                    "pid": os.getpid(),
                    "argv": argv,
                }
            ),
            encoding="utf-8",
        )
        return 3

    assert run_detached(entry, ["--flag"], "run-plan") == 3
    observed = json.loads(record.read_text(encoding="utf-8"))
    assert observed["leads_session"] is True, "the round did not lead its own session"
    assert observed["pid"] != os.getpid(), "the round ran in the launching process"
    assert observed["argv"] == ["--flag"]


def test_signalled_round_is_relayed_as_its_own_exit_status() -> None:
    def entry(_argv: list[str] | None) -> int:
        # The round takes the signal a torn-down launcher would have sent it, under the
        # default disposition it inherits, so what the parent relays is a real 128+N.
        os.kill(os.getpid(), signal.SIGTERM)
        return 0

    assert run_detached(entry, None, "run-plan") == 128 + int(signal.SIGTERM)
