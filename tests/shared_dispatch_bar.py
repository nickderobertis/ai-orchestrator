"""Readers for the shared clauses every dispatch on this host is given.

`config/onejudge.base.yaml` states them once: `system_prompt` is the preamble the
worker reads before it does anything, and `user.persona` and `user.done_when` are the
review contract and the completion criterion its judge is handed. They reach the model
by different paths — one as a system prompt, the other two inside the supervisor's own
turn — which is why the journeys proving each one arrives are separate. What they share
is this file, so the readers of it live here once: a copy per journey would be somewhere
for two tests to disagree about what it says.

Read with readers written for these fields rather than with a YAML library: the
workspace installs none, and adding a parser as a dependency to read three blocks of a
file this repository writes is a worse trade than the few lines below, which state the
shape they accept and fail loudly when the file leaves it.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.root import REPO_ROOT

BASE_CONFIG = Path("config") / "onejudge.base.yaml"

#: Every block-scalar header a field here may open with. Which one it uses decides how
#: the value reaches the model — `>` folds its newlines to spaces, `|` keeps them — so
#: the readers below fold or keep to match, and a style change is not a test change.
FOLDING_HEADERS = (">", ">-", ">+")
LITERAL_HEADERS = ("|", "|-", "|+")
BLOCK_SCALAR_HEADERS = FOLDING_HEADERS + LITERAL_HEADERS


def _block_scalar(key: str) -> tuple[str, list[str]]:
    """The header a `key:` opens with, and the more-indented block it introduces.

    The block is returned with the scalar's own indentation stripped and nothing else
    touched, which is exactly what a YAML reader hands the value's consumer.
    """
    text = (REPO_ROOT / BASE_CONFIG).read_text(encoding="utf-8")
    lines = text.splitlines()
    opened = next(
        (
            index
            for index, line in enumerate(lines)
            if line.strip() in {f"{key}: {header}" for header in BLOCK_SCALAR_HEADERS}
        ),
        None,
    )
    assert opened is not None, (
        f"{BASE_CONFIG} no longer opens `{key}` as a block scalar, so this reader "
        "cannot state what that field says"
    )
    header = lines[opened].strip().split(": ", 1)[1]
    indent = len(lines[opened]) - len(lines[opened].lstrip())
    block: list[str] = []
    for line in lines[opened + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    inner = min((len(line) - len(line.lstrip()) for line in block if line.strip()), default=0)
    return header, [line[inner:] if line.strip() else "" for line in block]


def shared_completion_bar() -> str:
    """`user.done_when`, as the judge is given it.

    A folded scalar reaches the judge with its newlines as single spaces, so folding
    here is what makes comparing the value against a real prompt valid. A literal one
    would reach it with those newlines intact; both are accepted and both are compared
    against a whitespace-normalized prompt, so the field's style is free to move.
    """
    _, block = _block_scalar("done_when")
    bar = " ".join(" ".join(block).split())
    assert bar, f"{BASE_CONFIG} states an empty shared completion bar"
    return bar


def shared_judge_persona() -> str:
    """`user.persona`, as the judge is given it.

    The default review contract, which a persona delta in `personas/` replaces. Folded
    for the same reason the completion bar is: it reaches the judge as one line either
    way, so the field's style is free to move without moving what it says.
    """
    _, block = _block_scalar("persona")
    persona = " ".join(" ".join(block).split())
    assert persona, f"{BASE_CONFIG} states an empty judge persona default"
    return persona


def shared_agent_preamble() -> str:
    """`system_prompt`, as a dispatched worker is given it.

    A literal scalar, because this one is paragraphs: it reaches the worker's system
    prompt with its line structure intact and a persona's role appended after it, so
    the value is returned verbatim rather than folded.
    """
    header, block = _block_scalar("system_prompt")
    assert header in LITERAL_HEADERS, (
        f"{BASE_CONFIG} opens `system_prompt` as {header!r}, a folding scalar; the "
        "shared preamble is paragraphs and reaches a worker with its line structure "
        "intact, so this reader states the literal style it expects"
    )
    preamble = "\n".join(block).strip("\n")
    assert preamble, f"{BASE_CONFIG} states an empty shared agent preamble"
    return preamble
