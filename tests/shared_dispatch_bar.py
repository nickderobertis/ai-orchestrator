"""Readers for the shared clauses every dispatch on this host is given.

`config/onejudge.base.yaml` states them twice over: `system_prompt` is the preamble the
worker reads before it does anything, and `user.done_when` is the completion criterion
its judge is handed. They reach the model by different paths — one as a system prompt,
the other inside the supervisor's own turn — which is why the journeys proving each one
arrives are separate. What they share is this file, so the readers of it live here once:
a copy per journey would be somewhere for two tests to disagree about what it says.

`user.persona` is read here too, and only ever for its **absence**. It is the one field
of the three that a dispatch replaces rather than merges — by a bare name resolving to a
built-in role exactly as by a path into `personas/` — so anything stated there reaches no
dispatch, and a reader that returned its value would invite a caller to assert something
arrives that cannot.

The appendix is read here too, though it is no field of this file: it is the other half
of what one dispatch is handed, and :func:`phrases_in_both` compares the two. That lives
beside the readers rather than inside one test because both files' guards ask it.

Read with a reader written for this shape rather than with a YAML library: the
workspace installs none, and adding a parser as a dependency to read three blocks of a
file this repository writes is a worse trade. That reader is
`orchestrator.criteria_guard.block_scalar`, which `just check-plan` already needs to
read a persona fragment lifted out of a binary — so this file states the *shape* it
expects on top of it and fails loudly when the file leaves it, rather than carrying a
second copy of the parsing for the two of them to disagree over.
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.criteria_guard import APPENDIX, LITERAL_HEADERS, block_scalar, field
from orchestrator.root import REPO_ROOT

BASE_CONFIG = Path("config") / "onejudge.base.yaml"


def appendix_text() -> str:
    """`config/dispatch-appendix.md`, as a dispatched task carries it.

    Read here beside the base config's own fields because the one gate that needs
    both compares them: dispatch policy has one source, and knowing whether a
    statement stands in both files means holding both documents at once.
    """
    return (REPO_ROOT / APPENDIX).read_text(encoding="utf-8")


def _block_scalar(key: str) -> tuple[str, str]:
    """The header a `key:` opens with, and its block normalized for reading.

    Folded or literal to match that header, which is what makes comparing it against
    a whitespace-normalized prompt valid — and the only comparison a model's
    rendering of these fields supports.
    """
    read = block_scalar((REPO_ROOT / BASE_CONFIG).read_text(encoding="utf-8"), key)
    assert read is not None, (
        f"{BASE_CONFIG} no longer opens `{key}` as a block scalar, so this reader "
        "cannot state what that field says"
    )
    return read


def shared_completion_bar() -> str:
    """`user.done_when`, as the judge is given it.

    A folded scalar reaches the judge with its newlines as single spaces, so folding
    here is what makes comparing the value against a real prompt valid. A literal one
    would reach it with those newlines intact; both are accepted and both are compared
    against a whitespace-normalized prompt, so the field's style is free to move.
    """
    _, bar = _block_scalar("done_when")
    assert bar, f"{BASE_CONFIG} states an empty shared completion bar"
    return bar


def judge_persona_default() -> str | None:
    """`user.persona`'s value, or ``None`` when the base config states none.

    Returns rather than asserts, because the only caller wants the absence: this is the
    field a dispatch replaces, so a value here is a review contract nothing is judged
    against. Read in both shapes a caller could reintroduce it in — a block scalar and a
    single quoted line — so re-adding it under either fails the guard rather than one.
    """
    document = (REPO_ROOT / BASE_CONFIG).read_text(encoding="utf-8")
    return field(document, "persona")


def shared_agent_preamble() -> str:
    """`system_prompt`, as a dispatched worker is given it.

    A literal scalar, because this one is paragraphs: it reaches the worker's system
    prompt with its line structure intact and a persona's role appended after it, so
    the block's own lines are kept rather than folded together.
    """
    header, preamble = _block_scalar("system_prompt")
    assert header in LITERAL_HEADERS, (
        f"{BASE_CONFIG} opens `system_prompt` as {header!r}, a folding scalar; the "
        "shared preamble is paragraphs and reaches a worker with its line structure "
        "intact, so this reader states the literal style it expects"
    )
    assert preamble, f"{BASE_CONFIG} states an empty shared agent preamble"
    return preamble


#: Consecutive words that make a shared run a restatement rather than ordinary English.
#: Measured, not chosen: the longest run these two documents share incidentally is four.
POLICY_PHRASE_WORDS = 6


def _words(document: str) -> list[str]:
    """``document`` as bare lowercase words, with Markdown and YAML shape dropped.

    Emphasis and backticks go first: one file bolds a sentence the other writes plain.
    """
    return re.sub(r"[^0-9A-Za-z$/.-]+", " ", re.sub(r"[`*_>#]", " ", document)).lower().split()


def phrases_in_both(first: str, second: str, *, words: int = POLICY_PHRASE_WORDS) -> list[str]:
    """Every maximal run of ``words`` or more consecutive words standing in both documents.

    Maximal, so a copied sentence is reported once rather than as every window inside it.
    Only exact runs are found: a rule reworded from memory shares none, which is what the
    named-demand guards in `tests/test_shared_dispatch_bar.py` cover instead.
    """
    left = _words(first)
    right = f" {' '.join(_words(second))} "

    def occurs(run: list[str]) -> bool:
        # Padded on both sides so a run cannot match inside a longer word: without it
        # `a check` is found in `aa check`, and the gate reports a hit that is not one.
        return f" {' '.join(run)} " in right

    runs: list[str] = []
    for start in range(len(left) - words + 1):
        if not occurs(left[start : start + words]):
            continue
        end = start + words
        while end < len(left) and occurs(left[start : end + 1]):
            end += 1
        runs.append(" ".join(left[start:end]))
    return [
        run
        for index, run in enumerate(runs)
        if not any(run in other for other in runs[:index])
        and not any(run != other and run in other for other in runs[index + 1 :])
    ]
