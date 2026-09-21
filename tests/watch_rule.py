"""What `AGENTS.md`'s watch rule says about the engine's watch verb, read out of the rule.

`just watch` is `onepipeline watch` with every argument forwarded, so the one place this
repository restates that verb's interface is the rule a supervisor reads: the `--until`
vocabulary, the spelling of an unbounded wait, the option names, and which exit status
means which ending. The journeys and the drift gate that hold the engine to it read those
facts from here rather than keeping a table of their own, because a second table is a
copy nothing reconciles with the one a supervisor actually follows.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from orchestrator.root import REPO_ROOT

#: The manager's document, and the section the watch rule is stated under.
DOCUMENT = "AGENTS.md"
SECTION = "### Never let dispatched work run unwatched"

#: One ending as the rule states it: a status in backticks, then the engine's own word.
STATUS = re.compile(r"`(\d+)` ([a-z][a-z-]*)")
#: The sentence the statuses are stated in, up to its full stop.
STATUS_SENTENCE = re.compile(r"The watch returns (.*?)\.", re.DOTALL)
#: The clause the `--until` vocabulary is stated in, up to its semicolon.
UNTIL_SENTENCE = re.compile(r"`--until` takes (.*?);", re.DOTALL)
#: The spelling the rule gives a wait with no bound.
UNBOUNDED = re.compile(r"`--timeout (\w+)` sets no bound on the wait")
#: An option the rule names, in backticks.
OPTION = re.compile(r"`(--[a-z][a-z-]*)")
#: The word the ending line carries the cursor under, as the rule quotes it.
CURSOR_LINE = re.compile(r"Re-arm from the `(\w+) <cursor>` its ending line")
#: What a heartbeat or an ending line says about unread planner surfaces, as the engine
#: renders it; the rule quotes it as `N unread planner surface(s)`.
UNREAD = re.compile(r"(\d+) unread planner surface")
#: The engine's human ending line: the run, the ending, the unread clause, the cursor. The
#: ending is the condition's word, followed by the node for `node-settled`.
ENDING_LINE = re.compile(
    r"^-- watch (?P<run>\S+) (?P<ending>(?P<condition>[a-z-]+)(?: \S+)?) {2}"
    r"(?P<unread>.*?) {2}cursor (?P<cursor>\S+)$"
)
#: The engine's human heartbeat line.
HEARTBEAT_LINE = re.compile(r"^-- watching (?P<run>\S+) ")


class Rule(NamedTuple):
    """The watch verb's interface as the rule states it."""

    #: Each ending the rule names, keyed by the engine's word, to the status it returns.
    statuses: dict[str, int]
    #: Every `--until` condition the rule offers, in the rule's order.
    conditions: tuple[str, ...]
    #: The spelling of a wait with no bound.
    unbounded: str
    #: Every option the rule names.
    options: frozenset[str]
    #: The word a caller anchors the cursor on in the ending line.
    cursor_word: str


def section() -> str:
    """The watch rule's section of `AGENTS.md`, whitespace collapsed."""
    prose = (REPO_ROOT / DOCUMENT).read_text(encoding="utf-8")
    assert prose.count(f"\n{SECTION}\n") == 1, f"{DOCUMENT} does not state {SECTION!r} once"
    body = prose.split(f"\n{SECTION}\n", 1)[1]
    cut = re.search(r"(?m)^#{1,3}\s", body)
    return " ".join((body if cut is None else body[: cut.start()]).split())


def rule() -> Rule:
    """Read the rule, failing by name on any part a reader could not find in it."""
    text = section()
    sentence = STATUS_SENTENCE.search(text)
    assert sentence is not None, f"{DOCUMENT}'s watch rule no longer states its exit statuses"
    statuses = {word: int(code) for code, word in STATUS.findall(sentence.group(1))}
    assert statuses, f"{DOCUMENT}'s watch rule states no `<status>` <ending> pair"
    until = UNTIL_SENTENCE.search(text)
    assert until is not None, f"{DOCUMENT}'s watch rule no longer states its `--until` vocabulary"
    conditions = tuple(re.findall(r"`([^`]+)`", until.group(1)))
    unbounded = UNBOUNDED.search(text)
    assert unbounded is not None, f"{DOCUMENT}'s watch rule no longer spells an unbounded wait"
    cursor = CURSOR_LINE.search(text)
    assert cursor is not None, f"{DOCUMENT}'s watch rule no longer says where to re-arm from"
    return Rule(
        statuses=statuses,
        conditions=conditions,
        unbounded=unbounded.group(1),
        options=frozenset(OPTION.findall(text)),
        cursor_word=cursor.group(1),
    )
