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

Read with a reader written for this shape rather than with a YAML library: the
workspace installs none, and adding a parser as a dependency to read three blocks of a
file this repository writes is a worse trade. That reader is
`orchestrator.criteria_guard.block_scalar`, which `just check-plan` already needs to
read a persona fragment lifted out of a binary — so this file states the *shape* it
expects on top of it and fails loudly when the file leaves it, rather than carrying a
second copy of the parsing for the two of them to disagree over.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.criteria_guard import LITERAL_HEADERS, block_scalar, field
from orchestrator.root import REPO_ROOT

BASE_CONFIG = Path("config") / "onejudge.base.yaml"


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
