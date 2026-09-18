"""The planner channel an observer member is judged over is the bus binding, on the run's channel.

onejudge asks a judge side three things, and only one of them is a supervisor ruling. Two
— `evals` and `assessment` — a persona can decline, and
`tests/test_planner_channel_personas.py` is where every channel-served member is held to
declining them. The third, `user.done_when`, cannot be declined: `oneagentgraph` merges a
persona's bar as a second one beside the base's, so a `kind: onejudge` member always
carries one and onejudge always asks its judge side to score it once the conversation
ends. So that bar is closed by being **served**, and what serves it is
`onemessagebus serve surfaces --codec monitor`: the binding this host declares under
`codecs.monitor` in `config/onemessagebus.yaml` puts the criterion to the planner as a
`monitor-completion` question and relays their ruling as the score, under the grammar the
bus states in its `docs/codecs.md`.

What the binding answers each frame is proven by the journeys that drive it
(`tests/e2e/test_monitor_quiet_turn_e2e.py`). What is proven here is the wiring, and the
wiring has three seams each of which fails silently:

- **The command is the binding, and nothing stands between it and onejudge.** The graph
  names an argv, and a wrapper that swallowed the serving session's exit status would keep
  a monitor whose agent side lost its turn reading as alive — exit 1 is how the binding
  ends that member.
- **It serves the queue this host's configuration declares its binding on.** `serve`
  refuses a queue other than `codecs.monitor.queue`, so a graph naming another one would
  kill the member on its first frame.
- **It asks where the manager answers.** The monitor's question is answered with `just
  channel-reply --correlation`, so the configuration file and the channel directory the
  binding is spawned with have to be the ones that recipe replies over — otherwise a
  ruling reaches a queue nobody is asking on, and every score degrades to `false`.

The launched journeys prove the three reach a real run
(`tests/e2e/test_monitor_survives_the_channel_e2e.py` for the score); this is the cheap
gate that says which file to fix.
"""

from __future__ import annotations

import re
import shlex
from typing import NamedTuple

from orchestrator.root import REPO_ROOT

#: The directory holding this repository's agent-graph documents.
GRAPHS = REPO_ROOT / "graphs"

#: This host's bus configuration, and the justfile whose `channel-reply` answers over it.
BUS_CONFIG = "config/onemessagebus.yaml"
JUSTFILE = REPO_ROOT / "justfile"

#: The binding the monitor's judge side serves onejudge's frames with, as
#: `config/onemessagebus.yaml` names it under `codecs`.
CODEC = "monitor"

#: The variable the engine names an observer member's run with. The reply recipe names
#: the same run as its first argument.
RUN_ID_ENV = "ONEPIPELINE_RUN_ID"

#: How a graph document names a member, and how deep the fields this reads are nested.
MEMBERS_KEY = "members:"
MEMBER_INDENT = 2
FIELD_INDENT = 4


class JudgeCommand(NamedTuple):
    """One member's `judge.command`, as the graph document spells it."""

    #: Where it is declared, for a failure that names the file to fix.
    graph: str
    #: The member's name in that document.
    member: str
    #: The argv, one element per list item, with folded scalars joined as YAML folds them.
    argv: list[str]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _argv(body: list[str]) -> list[str] | None:
    """The `judge.command` list under one member's lines, or `None` when it has none.

    Read with an indentation walk rather than a YAML library, as every other reader of
    these documents here does: the workspace installs none, and `oneagentgraph validate`
    — which `just check` runs over `graphs/` — is what holds them well-formed. A folded
    `>-` item is joined the way YAML folds it, one space per line break.
    """
    lines = [line for line in body if line.strip() and not line.lstrip().startswith("#")]
    try:
        judge = next(
            index
            for index, line in enumerate(lines)
            if _indent(line) == FIELD_INDENT and line.strip() == "judge:"
        )
    except StopIteration:
        return None
    block = []
    for line in lines[judge + 1 :]:
        if _indent(line) <= FIELD_INDENT:
            break
        block.append(line)
    opened = next((i for i, line in enumerate(block) if line.strip() == "command:"), None)
    if opened is None:
        return None
    items: list[str] = []
    item_indent: int | None = None
    folding = False
    for line in block[opened + 1 :]:
        if _indent(line) <= _indent(block[opened]):
            break
        stripped = line.strip()
        if stripped.startswith("- ") and (item_indent is None or _indent(line) == item_indent):
            item_indent = _indent(line)
            value = stripped[2:]
            folding = value == ">-"
            items.append("" if folding else value)
        elif folding and item_indent is not None and _indent(line) > item_indent:
            items[-1] = f"{items[-1]} {stripped}".strip()
        else:
            raise AssertionError(f"unrecognised `judge.command` line: {line!r}")
    return items


def _judge_commands() -> list[JudgeCommand]:
    """Every member of every shipped graph whose judge side is a command, discovered."""
    found: list[JudgeCommand] = []
    for document in sorted(GRAPHS.glob("*.yaml")):
        lines = document.read_text(encoding="utf-8").splitlines()
        if MEMBERS_KEY not in lines:
            continue
        name: str | None = None
        blocks: dict[str, list[str]] = {}
        for line in lines[lines.index(MEMBERS_KEY) + 1 :]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if _indent(line) == 0:
                break
            if _indent(line) == MEMBER_INDENT and line.rstrip().endswith(":"):
                name = line.strip().rstrip(":")
                blocks[name] = []
            elif name is not None:
                blocks[name].append(line)
        for member, body in blocks.items():
            argv = _argv(body)
            if argv is not None:
                found.append(JudgeCommand(str(document.relative_to(REPO_ROOT)), member, argv))
    return found


def judge_argv(graph: str, member: str) -> list[str]:
    """The `judge.command` argv one shipped graph declares for one member, as written.

    Public because the launched journeys spawn exactly this argv against a real run root:
    a journey that restated it would go on passing after the graph moved off it.
    """
    declared = [
        command.argv
        for command in _judge_commands()
        if command.graph == graph and command.member == member
    ]
    assert len(declared) == 1, f"{graph} declares {len(declared)} `judge.command` for `{member}`"
    return declared[0]


def _served(command: JudgeCommand) -> list[str]:
    """The words the member's `bash -c` script runs, with the shell's own quoting removed."""
    assert command.argv[:2] == ["bash", "-c"] and len(command.argv) == 3, (
        f"{command.graph}'s `{command.member}` judge command is {command.argv}, not one "
        "`bash -c` script; the run's channel directory is composed from the environment "
        "the engine exports, which only a shell can expand"
    )
    return shlex.split(command.argv[2])


def _flag(words: list[str], name: str) -> str:
    assert words.count(name) == 1, f"`{name}` is not named exactly once in {words}"
    return words[words.index(name) + 1]


def test_a_shipped_graph_serves_a_member_over_the_planner_channel() -> None:
    """Every assertion below is a loop over what this finds, so it has to find one."""
    served = [command for command in _judge_commands() if "onemessagebus serve" in command.argv[-1]]
    assert served, (
        f"no member of any document in {GRAPHS.relative_to(REPO_ROOT)} runs its judge side "
        "through `onemessagebus serve`, so every assertion in this file is vacuous; check this "
        "reader against those documents"
    )


def test_every_command_judge_side_is_the_bus_codec_execed_in_the_members_place() -> None:
    """The binding's exit status is the member's own, so a lost turn really ends the member."""
    for command in _judge_commands():
        words = _served(command)
        assert words[:3] == ["exec", "onemessagebus", "serve"], (
            f"{command.graph}'s `{command.member}` judge side runs {words[:3]}, not `exec "
            "onemessagebus serve`. Anything else either is not the planner channel's binding "
            "or stands between the binding and onejudge, where exit 1 — the binding reporting "
            "a turn the agent side lost — would stop ending the member"
        )
        assert _flag(words, "--codec") == CODEC, (
            f"{command.graph}'s `{command.member}` is served by codec "
            f"{_flag(words, '--codec')!r}; only `{CODEC}` is the binding this host declares "
            "for onejudge's frames, serving the `judge` op every two-party member is asked"
        )


def test_the_binding_serves_the_queue_this_configuration_declares_for_it() -> None:
    """`serve` refuses a queue other than the binding's own, on the member's first frame."""
    config = (REPO_ROOT / BUS_CONFIG).read_text(encoding="utf-8")
    declared = re.search(
        rf"^codecs:\n(?:\s*#.*\n)*  {CODEC}:\n(?:    .*\n)*?    queue: (\S+)$", config, re.MULTILINE
    )
    assert declared is not None, f"{BUS_CONFIG} declares no `codecs.{CODEC}.queue`"
    for command in _judge_commands():
        words = _served(command)
        queue = words[3]
        assert queue == declared.group(1), (
            f"{command.graph}'s `{command.member}` serves queue {queue!r} while {BUS_CONFIG} "
            f"declares the {CODEC} binding on {declared.group(1)!r}"
        )
        assert _flag(words, "--config") == BUS_CONFIG, (
            f"{command.graph}'s `{command.member}` reads {_flag(words, '--config')!r}, not "
            f"{BUS_CONFIG}, which every launch hands the engine and `just channel-reply` "
            "replies over"
        )


def test_the_binding_asks_on_the_channel_the_reply_recipe_answers_on() -> None:
    """The monitor's question and the manager's ruling meet in one directory, over one file.

    Held against the `channel-reply` recipe's own line rather than a restated path: the
    two are one decision made twice, and a run whose binding asked somewhere else would read
    every manager's score as a wait that elapsed.
    """
    body = re.search(
        r"^channel-reply run \*args:\n((?:    .*\n|\n)*?)(?=^\S)",
        JUSTFILE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert body is not None, "the justfile no longer declares `channel-reply run *args`"
    verb = re.search(r"onemessagebus reply (\w+)", body.group(1))
    channel = re.search(r"--config (\S+) --transport-dir (\S+)", body.group(1))
    assert verb is not None and channel is not None, (
        "the justfile's `channel-reply` no longer answers with `onemessagebus reply <queue>` "
        f"over a named `--config` and `--transport-dir`:\n{body.group(1)}"
    )
    replied_queue = verb.group(1)
    replied_config = channel.group(1)
    # The recipe names its run `$run`; the binding names the same run by the variable the
    # engine exports to an observer member. `shlex` removes the quoting and a closing
    # array parenthesis the recipe's shell line carries.
    replied_dir = shlex.split(channel.group(2).rstrip(")"))[0].replace("$run", f"${RUN_ID_ENV}")
    for command in _judge_commands():
        words = _served(command)
        assert (words[3], _flag(words, "--config"), _flag(words, "--transport-dir")) == (
            replied_queue,
            replied_config,
            replied_dir,
        ), (
            f"{command.graph}'s `{command.member}` asks on queue {words[3]!r} over "
            f"{_flag(words, '--config')!r} in {_flag(words, '--transport-dir')!r}, while "
            f"`just channel-reply` answers on {replied_queue!r} over {replied_config!r} in "
            f"{replied_dir!r}"
        )
