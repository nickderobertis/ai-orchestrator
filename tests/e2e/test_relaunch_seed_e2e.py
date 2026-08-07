"""What a relaunch carries forward, read back through the real history CLI.

The seed a relaunched dispatch is given comes out of oneharness' own history store,
through the real `oneharness history` command — so what a store actually contains,
and what this reader makes of it, is only answerable against a real one. The store
here is written in oneharness' own line format by the suite's single writer, and the
CLI reading it is production's.

The whole journey — a workstream that dies to a provider and is relaunched with this
seed in its prompt — is in test_lifecycle_e2e.py.
"""

# llmlint: ignore-file[e2e_not_mocked] The history store and the `oneharness history` CLI
# reading it are both real. One test makes a *listed* session's file unreadable, which is
# the live-store race (a session swept between the list and the read) and the one branch a
# store cannot be asked to produce on demand.
# llmlint: ignore-file[tests_mirror_real_usage] The user-facing path — a workstream that
# dies to a provider and is relaunched with this seed in its prompt — is driven through
# `run_repo_task` in test_lifecycle_e2e.py, which is what proves the seed reaches an agent
# at all. What these hold is the *bound*, and the bound is only visible against a store a
# dispatch cannot be asked to produce: twenty recorded turns of five thousand characters
# each, a session listed and then swept, a turn that recorded neither half. Driving each
# through a real lifecycle would need a paid agent to author that transcript first, and
# would still assert on the same rendering this calls directly.

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from history_store import write_worker_session

from orchestrator.history import HistoryError, session_records
from orchestrator.relaunch import SEED_CHARS, SEED_TURN_CHARS, transcript_seed

SESSION = "feature/branch@0123456789:main"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated oneharness history store the real CLI will read."""
    history_dir = tmp_path / "history"
    written = history_dir / "relaunch-project"
    written.mkdir(parents=True)
    monkeypatch.setenv("ONEHARNESS_HISTORY_DIR", str(history_dir))
    return written


def test_a_recorded_conversation_is_rendered_as_prompt_context(store: Path, tmp_path: Path) -> None:
    write_worker_session(
        store / "dead-20260806T120000Z-1.jsonl",
        project=tmp_path,
        name=SESSION,
        prompt="Measure the lifecycle cost.",
    )

    seed = transcript_seed(SESSION)

    assert seed is not None
    assert "asked: Measure the lifecycle cost." in seed
    assert "answered: Implemented the unified view." in seed


def test_a_conversation_nothing_recorded_seeds_nothing(store: Path) -> None:
    """The death before the first turn: nothing to carry, and that is not a failure."""
    assert transcript_seed(SESSION) is None


def test_another_conversations_turns_are_never_carried_into_this_one(
    store: Path, tmp_path: Path
) -> None:
    write_worker_session(
        store / "other-20260806T120000Z-1.jsonl",
        project=tmp_path,
        name="some-other-step",
        prompt="Not this workstream's work.",
    )

    assert transcript_seed(SESSION) is None


def test_a_history_command_that_cannot_list_sessions_still_lets_the_relaunch_go(
    store: Path, tmp_path: Path
) -> None:
    """The store this host cannot read at all: unseeded, never held up.

    Driven through a real `oneharness` that fails rather than by patching the listing
    out, because the failure is a subprocess one — a CLI that is broken, absent, or
    pointed at a store it cannot open, which is what an operator actually hits. The
    conversation is written and provably findable first, so the `None` here can only
    mean the listing failed and not that there was nothing to carry.
    """
    write_worker_session(
        store / "dead-20260806T120000Z-1.jsonl",
        project=tmp_path,
        name=SESSION,
        prompt="Measure the lifecycle cost.",
    )
    assert transcript_seed(SESSION) is not None, "the seed must be findable before it is denied"

    broken = tmp_path / "broken-oneharness"
    broken.write_text(
        "#!/usr/bin/env bash\necho 'history store unavailable' >&2\nexit 1\n", encoding="utf-8"
    )
    broken.chmod(broken.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    assert transcript_seed(SESSION, oneharness_bin=str(broken)) is None


def test_a_session_file_that_disappeared_falls_through_to_the_next(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live-store race: a session listed, then swept before it could be read."""
    write_worker_session(
        store / "newer-20260806T130000Z-1.jsonl",
        project=tmp_path,
        name=SESSION,
        prompt="the newer one",
    )
    write_worker_session(
        store / "older-20260806T120000Z-1.jsonl",
        project=tmp_path,
        name=SESSION,
        prompt="the older one",
    )

    def refuse_the_newer(session: object) -> list[dict[str, object]]:
        if "newer" in str(getattr(session, "path", "")):
            raise HistoryError("cannot read history session")
        # `object` above so this stands in for the module attribute whatever the
        # caller passes; the real reader wants a `HistorySession`, and every value
        # reaching here is one that `all_sessions` just produced.
        return session_records(session)  # type: ignore[arg-type]

    monkeypatch.setattr("orchestrator.relaunch.session_records", refuse_the_newer)

    seed = transcript_seed(SESSION)

    assert seed is not None
    assert "the older one" in seed


def _rewritten_turns(store: Path, name: str, project: Path, turns: list[dict[str, object]]) -> None:
    """Write a many-turn session by repeating the suite's own recorded run shape.

    Derived from `history_store.write_worker_session` rather than restated: that
    module exists so the store has one shape, and a hand-written record here would be
    a second copy the real CLI could quietly refuse to list. The first turn keeps the
    written run's own ``history_id`` because the tool-call line beside it names that
    id — rewriting it orphans the event and the CLI stops listing the session at all.
    """
    seed_path = store / f"{name}-20260806T120000Z-1.jsonl"
    write_worker_session(seed_path, project=project, name=SESSION)
    lines = [json.loads(line) for line in seed_path.read_text(encoding="utf-8").splitlines()]
    template = next(line for line in lines if line.get("type") == "run")
    written = [line for line in lines if line.get("type") != "run"]
    written.extend(
        {
            **template,
            **({} if index == 0 else {"history_id": f"{template['history_id']}-{index}"}),
            **turn,
        }
        for index, turn in enumerate(turns)
    )
    seed_path.write_text("".join(json.dumps(line) + "\n" for line in written), encoding="utf-8")


def test_a_long_transcript_is_bounded_per_turn_and_overall(store: Path, tmp_path: Path) -> None:
    """A seed is a prompt, not a restore: it must not push out the task behind it."""
    _rewritten_turns(
        store,
        "long",
        tmp_path,
        [
            {
                "prompt": f"turn {index} asked " + "a" * 5_000,
                "text": f"turn {index} answered " + "b" * 5_000,
            }
            for index in range(20)
        ],
    )

    seed = transcript_seed(SESSION)

    assert seed is not None
    assert len(seed) <= SEED_CHARS
    assert all(len(part) <= SEED_TURN_CHARS for part in seed.split("\n\n"))
    # What survives the bound is the tail — the turn that was interrupted — and both
    # halves of it, because a long prompt must not push the answer out.
    assert "turn 0 asked" not in seed
    assert "turn 19 asked" in seed
    assert "turn 19 answered" in seed


def test_a_record_carrying_no_turn_contributes_nothing(store: Path, tmp_path: Path) -> None:
    """A turn that never completed may record neither half, and often does not."""
    _rewritten_turns(store, "empty", tmp_path, [{"prompt": "   ", "text": None}])

    assert transcript_seed(SESSION) is None
