"""A worker's judge side can be a list of judges, on the onejudge this host pins.

The adoption this proves is `config/onejudge.version`'s: from it, a run's judge side may
be a **panel** — several judges run against the same worker turn at once, the worker
handed one message combining the instructions of every judge that did not complete, each
under a ``## Judge `<label>` (<kind>)`` header, and the report recording which judge
decided what on each turn. This host stacks no judge on any dispatch, so nothing else in
the suite would notice a pinned CLI that had lost it; `docs/onejudge-integration.md`'s
"The judge side may be a list of judges" is the account this holds.

Every journey runs the pinned `onejudge run` — through the installed `onejudge_sdk`, which
spawns the CLI and validates what it writes against the release's own contract — offline,
with `judge_protocol_double.py` answering `docs/protocol.md` on each side. The doubles are
at the provider boundary and nowhere above it: the panel, its concurrency, its combined
message and its report are the CLI's own.

Three shapes, because onejudge's own `docs/judges.md` at the adopted release draws the
line between them: *`judge:` is the one-element shorthand for `judges: [..]`*, and *a
panel of one judge … is byte-identical to a bare provider … It adds the per-judge
record*, while *a provider that is not a panel records none*. So a single `judge:` under
`split` records exactly one decision per turn with no header in the worker's message, and
only a single provider answering both sides — the non-panel shape — records no
`judge_decisions` at all.

It reads the onejudge this host installed, which lives outside the workspace and so
outside every `nx.json` key, so it runs in the uncached tier.
"""

# The findings these answer are about which Nx project owns this file, so it is the file
# that is suppressed and a project split that would resolve it. The journeys run the
# `onejudge` this host installed, which lives outside the workspace and so outside every
# `nx.json` key: the uncached `orchestrator:test-checkouts` tier exists for exactly that,
# selected by `reads_checkouts`, and a second Nx project would need its own key over the
# same nothing. `tests/test_nx_cache_scope.py` holds the marker to routing rather than to
# a shortcut.
# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] see above
# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] see above

from __future__ import annotations

import asyncio
import functools
import subprocess
import sys
from pathlib import Path

import pytest
from onejudge_sdk import (
    JudgedTurn,
    OneJudge,
    OneJudgeProcessError,
    ProviderConfig,
    RunConfig,
    RunResult,
    UserConfig,
)
from project_fixtures import helper

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The provider command every side of these runs speaks through. It stands in for the paid
#: model only, at onejudge's documented provider boundary (`docs/protocol.md`): the panel
#: under test — its concurrency, combined message and report — is the pinned CLI's own.
# llmlint: ignore[e2e_not_mocked] Only the paid model is substituted; see the comment above.
DOUBLE = helper("judge_protocol_double.py")
#: The CLI the project environment installed at `config/onejudge.version`, beside the
#: interpreter running this suite — never whichever `onejudge` another checkout put first
#: on `PATH`.
PINNED_ONEJUDGE = Path(sys.executable).with_name("onejudge")
#: What the worker is asked; the doubles never read it, and a report carries it back.
TASK = "Rename the helper and keep every caller working."
#: Long enough for a judge that continues once and then completes, with room to spare so
#: a panel that failed to complete reads as a wrong decision rather than a turn cap.
MAX_TURNS = 4
#: A run that answers nothing inside this is a failure of the CLI, not a slow model.
RUN_TIMEOUT_SECONDS = 60


def _side(name: str, continue_for: int, label: str | None = None) -> ProviderConfig:
    """One provider entry answered by the double, as `docs/protocol.md` names it."""
    side = ProviderConfig(
        kind="command", command=[sys.executable, str(DOUBLE), name, str(continue_for)]
    )
    if label is not None:
        side["label"] = label
    return side


def _config(provider: ProviderConfig) -> RunConfig:
    return RunConfig(
        provider=provider,
        user=UserConfig(
            persona="A reviewer holding a rename to every caller.",
            done_when="every caller uses the new name",
            max_turns=MAX_TURNS,
        ),
    )


def _run(config: RunConfig, cwd: Path) -> RunResult:
    client = OneJudge(executable=str(_pinned_onejudge()))
    return asyncio.run(client.run(config, TASK, cwd=str(cwd), timeout=RUN_TIMEOUT_SECONDS))


def _messages(result: RunResult) -> list[str]:
    return [message["content"] for message in result.raw["transcript"]["messages"]]


def _decided(turns: list[JudgedTurn]) -> list[tuple[int, list[tuple[str, str, str]]]]:
    """Each judged turn as its number and every (label, kind, decision) on it, in order."""
    return [
        (
            turn["turn"],
            [(one["judge"], one["kind"], one["decision"]) for one in turn["decisions"]],
        )
        for turn in turns
    ]


@functools.cache
def _pinned_onejudge() -> Path:
    """The CLI these journeys run is the release this host adopted, or nothing is proven.

    A failure rather than a skip: a journey that quietly ran an older onejudge would report
    the same green about a release nobody ran. `scripts/session-setup.sh` installs it.
    Checked once per worker process and before every run, rather than by a fixture, because
    it only reads.
    """
    adopted = (REPO_ROOT / "config" / "onejudge.version").read_text(encoding="utf-8").strip()
    assert PINNED_ONEJUDGE.exists(), (
        f"no onejudge beside {sys.executable}; run `scripts/session-setup.sh` so the project "
        f"environment installs the adopted {adopted}"
    )
    reported = subprocess.run(
        [str(PINNED_ONEJUDGE), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert reported == f"onejudge {adopted}", (
        f"{PINNED_ONEJUDGE} reports {reported!r}, not the onejudge {adopted} "
        "config/onejudge.version adopts"
    )
    return PINNED_ONEJUDGE


def test_a_judge_side_listing_two_judges_hands_the_worker_both_and_records_both(
    tmp_path: Path,
) -> None:
    """Two judges both continue on the first turn: one combined message, two decisions."""
    result = _run(
        _config(
            {
                "kind": "split",
                "skill": _side("worker", 0),
                "judges": [_side("alpha", 1, label="alpha"), _side("beta", 1, label="beta")],
            }
        ),
        tmp_path,
    )

    assert result.completed, result.stderr
    assert _decided(list(result.raw.get("judge_decisions", ()))) == [
        (1, [("alpha", "command", "continue"), ("beta", "command", "continue")]),
        (2, [("alpha", "command", "done"), ("beta", "command", "done")]),
    ]
    continued = _messages(result)[2]
    assert continued == (
        "## Judge `alpha` (command)\n\n"
        "alpha: a caller under tests/ still uses the old name.\n\n"
        "## Judge `beta` (command)\n\n"
        "beta: a caller under tests/ still uses the old name."
    ), continued


def test_a_single_judge_is_a_panel_of_one_whose_message_carries_no_header(
    tmp_path: Path,
) -> None:
    """`judge:` is the list's shorthand: one decision per turn, the message verbatim."""
    result = _run(
        _config({"kind": "split", "skill": _side("worker", 0), "judge": _side("reviewer", 1)}),
        tmp_path,
    )

    assert result.completed, result.stderr
    assert _decided(list(result.raw.get("judge_decisions", ()))) == [
        (1, [("command", "command", "continue")]),
        (2, [("command", "command", "done")]),
    ]
    assert _messages(result)[2] == "reviewer: a caller under tests/ still uses the old name."


def test_a_single_provider_answering_both_sides_records_no_judge_decisions(
    tmp_path: Path,
) -> None:
    """The non-panel shape — one provider for both sides — is judged and records none."""
    result = _run(_config(_side("reviewer", 1)), tmp_path)

    assert result.completed, result.stderr
    assert "judge_decisions" not in result.raw, result.raw.get("judge_decisions")
    assert _messages(result)[2] == "reviewer: a caller under tests/ still uses the old name."


def test_a_judge_that_cannot_run_fails_the_run_and_is_never_read_as_a_pass(
    tmp_path: Path,
) -> None:
    """One judge of two dies: the run fails, naming it, with the other's decision kept."""
    with pytest.raises(OneJudgeProcessError) as failed:
        _run(
            _config(
                {
                    "kind": "split",
                    "skill": _side("worker", 0),
                    "judges": [
                        _side("alpha", 0, label="alpha"),
                        _side("broken", -1, label="broken"),
                    ],
                }
            ),
            tmp_path,
        )

    failure = failed.value.failure
    assert failure is not None, failed.value.stderr
    assert _decided(list(failure.get("judge_decisions", ()))) == [
        (1, [("alpha", "command", "done"), ("broken", "command", "error")]),
    ]
    assert "supervise[broken]" in failed.value.stderr, failed.value.stderr


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]
# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]
