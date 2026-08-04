from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from orchestrator import provider_health
from orchestrator.dispatch import classify_provider_failure
from orchestrator.journal import NodeId, open_journal
from orchestrator.next_round import main_runs
from orchestrator.provider_health import IDENTITIES, failure_rollups, probe, render
from orchestrator.read_model import list_runs, run_detail
from orchestrator.runs import RunId, prepare_round, write_result
from orchestrator.status import main as status_main


def test_provider_failure_vocabulary_and_bounded_payload() -> None:
    quota = classify_provider_failure(
        "judge-side codex:alternate quota exhausted; resets Aug 8 " + "x" * 3000
    )
    assert quota is not None
    assert quota["side"] == "judge"
    assert quota["identity"] == "codex:alternate"
    assert quota["cause"] == "quota_mid_conversation"
    assert len(quota["raw_tail"]) == 2000

    stale = classify_provider_failure(
        "claude-code:alternate2 harness: No conversation found with session ID dead-beef"
    )
    assert stale is not None
    assert stale["cause"] == "stale_session_resume"
    assert stale["missing_session_id"] == "dead-beef"

    rate = classify_provider_failure(
        "agent claude-code:primary rate limit watchdog killed retry loop after 12 minutes"
    )
    assert rate is not None
    assert rate["cause"] == "rate_limit"
    assert rate["failure_kind"] == "rate_limit"
    assert rate["wait_seconds"] == 720

    structured = classify_provider_failure(
        'claude-code:primary harness exited 1 {"subtype":"error_during_execution",'
        '"errors":["first failure","second failure"]}'
    )
    assert structured is not None
    assert structured["cause"] == "harness_exit"
    assert structured["structured_error"] == {
        "subtype": "error_during_execution",
        "errors": ["first failure", "second failure"],
    }


def test_a_refusal_is_only_ever_attributed_to_a_configured_identity() -> None:
    """The classifier resolves against the gated roster, not a second copy of it.

    An identity it invented would read as capacity nobody can find: the rollup would
    name one thing and the provider-health block beside it another, for the same
    account. `codex:primary` is what an operator and a harness both write for the
    identity the configs spell `codex`, so it resolves to that — not to a sixth
    identity — and `codex:alternate` is never shortened to it.
    """
    for spelling, expected in (
        ("judge-side codex:primary quota exhausted", "codex"),
        ("judge-side codex quota exhausted", "codex"),
        ("agent-side codex:alternate quota exhausted", "codex:alternate"),
        ("agent-side claude-code:alternate2 quota exhausted", "claude-code:alternate2"),
    ):
        classified = classify_provider_failure(spelling)
        assert classified is not None, spelling
        assert classified["identity"] == expected, spelling
        assert classified["identity"] in IDENTITIES, spelling

    # An identity nothing configures is not guessed at from a harness name alone.
    invented = classify_provider_failure("harness failed (quota) for claude-code:alternate9")
    assert invented is not None
    assert invented["identity"] == "unknown"


def test_probe_keeps_all_configured_identities_and_renders_unknown(tmp_path: Path) -> None:
    """An answer that names one identity still accounts for all five."""
    payload = {
        "schema_version": "0.1",
        "identities": [
            {
                "harness": "codex",
                "availability": {
                    "state": "available",
                    "windows": [
                        {
                            "id": "weekly",
                            "usage": {"kind": "metered", "used_percent": 1.0},
                            "resets_at": "2026-08-08T00:00:00Z",
                        }
                    ],
                },
            }
        ],
    }
    snapshot = probe(cwd=tmp_path, read_usage=lambda _bin, _cwd: json.dumps(payload))
    assert [item["identity"] for item in snapshot["identities"]] == list(IDENTITIES)
    shown = render(snapshot)
    assert "codex: weekly binding, 100% used, resets 2026-08-08" in shown
    assert "claude-code:alternate: unknown" in shown


@pytest.mark.parametrize(
    "answer",
    ["", "not json at all", '{"identities": "not a list"}'],
    ids=["no-answer", "unparseable", "wrong-shape"],
)
def test_a_probe_that_cannot_answer_lists_every_identity_as_unknown(
    tmp_path: Path, answer: str
) -> None:
    """A refusing, timing-out, or nonsense-returning probe is degraded, not dropped."""
    snapshot = probe(cwd=tmp_path, read_usage=lambda _bin, _cwd: answer or None)

    assert [item["identity"] for item in snapshot["identities"]] == list(IDENTITIES)
    assert render(snapshot).splitlines()[1:] == [f"  {name}: unknown" for name in IDENTITIES]


def test_the_disable_switch_answers_unknown_without_reaching_a_provider(
    tmp_path: Path, monkeypatch
) -> None:
    """The suite and an offline host turn ambient probing off by name."""

    def refuse(_bin: str, _cwd: Path) -> str:
        raise AssertionError("a disabled probe must not reach the usage boundary")

    monkeypatch.setattr(provider_health, "_read_usage", refuse)
    monkeypatch.setenv(provider_health.PROBE_ENV, "0")
    assert [item["availability"]["state"] for item in probe(cwd=tmp_path)["identities"]] == [
        "unknown"
    ] * len(IDENTITIES)

    # The switch governs the ambient default only: a caller that states its own
    # boundary — every unit test above — is asking for that boundary, not for this.
    stated = probe(cwd=tmp_path, read_usage=lambda _bin, _cwd: '{"identities": []}')
    assert [item["identity"] for item in stated["identities"]] == list(IDENTITIES)


def test_failure_rollup_collapses_same_cause(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    journal = open_journal(run_dir, RunId("run"), 1)
    fact = {
        "side": "judge",
        "identity": "codex",
        "cause": "quota_mid_conversation",
        "reset_time": "Aug 8",
    }
    for node in ("a", "b", "c"):
        journal.append("node-failed", node=NodeId(node), detail={"failure_attribution": fact})
    assert failure_rollups(run_dir) == [
        "3 nodes failed on judge-side codex quota mid conversation, resets Aug 8"
    ]


def test_provider_health_crosses_cli_views_and_read_api(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The public views all consume the same real usage subprocess payload."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "oneharness"
    payload = {
        "schema_version": "0.1",
        "observed_at": "2026-08-04T00:00:00Z",
        "identities": [
            {
                "harness": "codex",
                "availability": {
                    "state": "available",
                    "windows": [
                        {
                            "id": "weekly",
                            "binding": True,
                            "usage": {"kind": "metered", "used_percent": 0.75},
                            "resets_at": "2026-08-08T00:00:00Z",
                        }
                    ],
                },
            },
            {
                "harness": "claude-code",
                "variant": "alternate",
                "availability": {"state": "unknown"},
            },
        ],
    }
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = usage ]; then\n'
        f"  printf '%s\\n' '{json.dumps(payload)}'\n"
        "else\n"
        "  printf '[]\\n'\n"
        "fi\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("ONEHARNESS_HISTORY_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    # The suite disables ambient probing; this journey is the one that means to spend
    # one, through the real wrapper and the identity indirections it sources.
    monkeypatch.setenv(provider_health.PROBE_ENV, "1")

    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "health"
    round_record = prepare_round(
        run_dir, {"tasks": [{"id": "work", "persona": "engineer", "task": "x"}]}
    )
    journal = open_journal(run_dir, RunId("health"), round_record.number)
    # A well-formed round: the node is declared before the round starts, which is
    # what the strict projection behind `run_detail` requires.
    journal.append(
        "node-added", detail={"definition": {"id": "work", "persona": "engineer", "task": "x"}}
    )
    journal.append("round-started", detail={"plan": {"schema_version": 3, "concurrency": 1}})
    journal.append("node-started", node=NodeId("work"))

    assert status_main(["health", "--runs-dir", str(runs_dir)]) == 0
    status_output = capsys.readouterr().out
    assert "Provider health:" in status_output
    assert "codex: weekly binding, 75% used, resets 2026-08-08" in status_output
    assert "claude-code:alternate: unknown" in status_output

    write_result(
        round_record.directory,
        {
            "schema_version": 6,
            "ok": True,
            "started_order": ["work"],
            "results": {"work": {"status": "done"}},
        },
    )
    assert main_runs(["--runs-dir", str(runs_dir)]) == 0
    assert "Provider health:" in capsys.readouterr().out

    served = list_runs(runs_dir, include_settled=True, oneharness_bin=str(fake))
    assert served["provider_health"]["identities"][2]["identity"] == "codex"
    assert (
        served["provider_health"]["identities"][2]["availability"]["windows"][0]["binding"] is True
    )

    # Both served envelopes carry it, so both are read here: a client that opens one
    # run rather than listing them must still see the capacity that explains its
    # failures, and the run-detail copy is the same snapshot rather than a stub.
    detail = run_detail(runs_dir, "health", oneharness_bin=str(fake))
    assert detail["provider_health"] == served["provider_health"]
    assert [item["identity"] for item in detail["provider_health"]["identities"]] == list(
        IDENTITIES
    )
