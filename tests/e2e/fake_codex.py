#!/usr/bin/env python3
"""A stand-in for the paid codex CLI: this repository's one faked boundary.

oneharness runs the selected harness's own binary, so pointing
``ONEHARNESS_BIN_CODEX`` here replaces exactly the paid provider and nothing else.
The real oneharness still selects it, spawns it, parses its stream, times and
prices the turn, and writes the history record the launch contract is read back
out of — which is what a smoke journey has to keep real to mean anything.

**`codex app-server` is refused as codex refuses it.** A launch carrying an argument
`codex app-server` does not accept — `--skip-git-repo-check`, which only `exec` takes, among
them — exits 2 with clap's ``error: unexpected argument '<arg>' found`` and starts nothing,
whatever any variable below says; `APP_SERVER_RELEASE` names the codex release the accepted
options were read from. It makes no directory check, as codex makes none for `app-server`.

Ten environment variables steer it, and each exists because a journey has to
tell one outcome from another deterministically:

* ``FAKE_CODEX_SERVES_APP_SERVER`` — an accepted `app-server` launch is served as codex
  serves a per-dispatch one: JSON-RPC on stdio until the client closes it, each turn answered
  from ``FAKE_CODEX_ANSWERS`` and recorded as an `exec` turn is. It is how a journey runs a
  controlled turn — a dispatch's supervisor — on this provider through the real `oneharness`.
  ``FAKE_CODEX_REFUSAL_LOG`` beside it names a file each refused `app-server` launch appends
  its argv and refusal to, since that stderr reaches no record a journey can read.

* ``FAKE_CODEX_ATTEMPT_LOG`` names a file this appends one line to per launch,
  which is how a journey counts the turns a chain actually spent.
* ``FAKE_CODEX_UNAVAILABLE_ATTEMPTS`` — the first N launches die the way a
  contended host made the real provider die: started, and then failed. *Which*
  turn contention kills is not something generating load can decide, so the count
  is what makes "the first launch failed and a later one did not" deterministic
  while every other party stays real.
* ``FAKE_CODEX_OMIT_USAGE`` — the launch succeeds and returns a turn carrying no
  token accounting, which is a broken recorded contract rather than weather.
* ``FAKE_CODEX_ANSWERS`` — a JSON array of the answers to give, one per launch,
  the last repeating once they run out. It exists for a **structured** run: when
  oneharness carries a schema it validates this text against it and re-prompts on
  failure, so telling one launch from the next by its answer is the only way a
  journey can watch that retry happen. Absent, every launch answers ``smoke-ok``.
* ``FAKE_CODEX_HOLD_SECONDS`` — the launch records itself, then holds that long
  before answering. It exists so a journey can interrupt a turn that is provably
  *in flight*: signalling a caller and hoping the turn had started is a race whose
  failure mode is a green test, and the attempt log is the only moment a journey can
  prove the provider was reached. Absent, a launch answers immediately.
  ``FAKE_CODEX_HOLD_RELEASE`` beside it names a file whose existence ends the hold
  early, so a journey holds the turn until it says so and the seconds are only a bound
  for a journey that died; ``FAKE_CODEX_HOLDING`` names a file the held launch writes
  its pid to before it holds, which is how a journey reads that the turn is still in
  flight — its process alive — at a moment of its choosing.
* ``FAKE_CODEX_FAIL_AFTER_TURN`` — the launch emits its whole billed turn and
  *then* exits non-zero saying something no classifier recognizes. It is the
  counterpart of ``FAKE_CODEX_UNAVAILABLE_ATTEMPTS``: both leave a failure nothing
  can name, and they differ only in whether the provider has anything to show for
  itself, which is the one reading a chain publishes rather than derives.
* ``FAKE_CODEX_PROMPT_LOG`` names a file this appends one JSON record to per
  launch, carrying the prompt the provider was actually given, the argv it was
  spawned with — which is where the approval mode a role file asked for arrives, as
  codex's own ``--sandbox`` — and every file the launch opened under
  ``FAKE_CODEX_READ_LISTED_FOR``. It is how a journey
  reads the prompt of a turn nothing else can observe: since oneagentgraph 0.2.18 a
  single-sided ``kind: oneharness`` member's turn is an in-process
  ``oneharness_core`` call rather than a spawned CLI, so
  ``ONEAGENTGRAPH_ONEHARNESS_BIN`` — and with it ``tests/e2e/fake_backend.py`` — is
  not on that member's path at all. The provider binary is, and this is it. These
  single-sided members here take that path: ``graphs/pr-author.yaml``'s drafter
  and ``graphs/review-reply.yaml``'s.
* ``FAKE_CODEX_RUN_ON_MARKER`` names a JSON file mapping a marker to the argument
  vectors a turn whose prompt carries that marker runs, in order, where the turn runs.
  It is ``tests/e2e/fake_backend.py``'s ``FAKE_BACKEND_RUN_ON_MARKER`` at the one seam a
  single-sided member still reaches: ``graphs/follow-up.yaml``'s follow-up agent is
  such a member, and what that agent *does* is run programs — write a ticket, validate
  it, copy it onto a board — so this substitutes the model's choice of commands and
  nothing below it. The programs are the real ones and every record they leave is theirs.
  ``FAKE_CODEX_RUN_ON_MARKER_LOG`` beside it names a file each command's argv, exit status
  and output are appended to, one JSON line apiece: this process's own stderr reaches no
  record a journey can read, so that file is how a failed command explains itself.

* ``FAKE_CODEX_READ_LISTED_FOR`` names a marker, and makes a launch whose prompt lists
  task files — the plan-level review's ``task file: <path>`` lines, each under its
  ``### Node `<id>``` heading — do what a reviewer reading through those paths does:
  open each listed file from inside the turn, and, where a line of one carries the
  marker, answer a refusal whose finding names that node and quotes the line. It is
  how a journey proves the listed path is real, readable in the turn, and carries the
  detail the prompt left out — the model's choice of which file to open is the one
  thing substituted. A prompt listing no task file, or files carrying no marker, is
  answered as any other launch is.

* ``FAKE_CODEX_LLMLINT_FAIL`` scripts the one launch that is not a reviewer's: an
  llmlint judge batch, recognized by the ``## Target files`` and ``## Rules to evaluate``
  sections llmlint's prompt carries, is answered in the shape llmlint's schema asks for,
  every rule the batch lists holding — except each rule this JSON object names, mapped to
  the ``file``, ``line`` and ``message`` of the violation it is answered with, or to a list
  of them for a rule failing in several files. A violation
  is reported only in a file the batch lists and the rule's ``Scope:`` line covers, as a
  model reading the prompt would. The batch is answered outside the scripted launches:
  it is not counted in ``FAKE_CODEX_ATTEMPT_LOG``, takes no ``FAKE_CODEX_ANSWERS`` entry,
  and is recorded in ``FAKE_CODEX_LLMLINT_PROMPT_LOG`` rather than ``FAKE_CODEX_PROMPT_LOG``,
  so a journey scripting or reading a reviewer's turns in order is not shifted by an
  llmlint judge running beside it. ``FAKE_CODEX_LLMLINT_OMIT`` beside it is a JSON list of
  rule names that batch's answer leaves out altogether, ``["*"]`` leaving out every one:
  the incomplete reply a model can give, which llmlint has to refuse rather than read as a
  pass. ``FAKE_CODEX_LLMLINT_LEAVE_TEMPORARY`` names a file each such batch appends the
  path of a ``codex-bwrap-synthetic-mount-targets-<pid>`` directory to, which it makes in
  its temporary directory and leaves there, as the real codex's sandbox does on every
  turn; it is how a journey proves the caller hands the judge a temporary directory it
  removes.

Keep this deterministic and stdlib-only — this file *is* the provider binary.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal, NamedTuple, NewType, NotRequired, TypedDict


class AgentMessage(TypedDict):
    """The one item this provider completes: the turn's answer text."""

    type: Literal["agent_message"]
    text: str


class Usage(TypedDict):
    """A turn's token accounting, which is the evidence it was billed."""

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


#: What every billed turn of this provider bills.
TURN_USAGE = Usage(input_tokens=4, cached_input_tokens=0, output_tokens=1)


class TurnEvent(TypedDict):
    """One line of the codex event stream, as this provider emits it.

    One shape rather than a union per `type`, because the four events this file
    emits differ only by which optional field they carry, and a reader here reads
    `type` first either way. `FAKE_CODEX_OMIT_USAGE` drops `usage` from the whole
    stream, which is why it is optional at all.
    """

    type: Literal["turn.started", "thread.started", "item.completed", "turn.completed"]
    thread_id: NotRequired[str]
    item: NotRequired[AgentMessage]
    usage: NotRequired[Usage]


class PromptRecord(TypedDict):
    """One launch, as ``FAKE_CODEX_PROMPT_LOG`` records it: what it was given and opened."""

    prompt: str
    argv: list[str]
    read: list[str]


class Finding(TypedDict):
    """One criterion a refusal names, in the verdict schema's two fields."""

    criterion: str
    why: str


class Refusal(TypedDict):
    """The verdict a launch answers when the files it opened carried the marker."""

    passes: Literal[False]
    findings: list[Finding]


#: What a launch answers when no journey scripted one.
DEFAULT_ANSWER = "smoke-ok"

#: The options codex reads a configuration override from, each taking the next word;
#: `tests/test_command_ceiling_providers.py` holds both to the installed codex's parser.
CONFIG_OPTIONS = ("-c", "--config")

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] By the manager's ruling (c3.0) this list is pinned to the codex release `APP_SERVER_RELEASE` names and re-taken by hand, with clap's refusal wording, on the codex bump that moves it: a suite gate on the installed binary would make this repository's tests depend on whichever codex a host happens to have, and the task makes the comparison with `codex app-server --help` a one-off quoted in its report.  # noqa: E501 - a directive is one line
# What `codex app-server` accepts, and the one place this repository states it: every
# option and subcommand `codex app-server --help` lists at `APP_SERVER_RELEASE`. Anything
# else is refused before a server starts, as clap refuses it, so a role file carrying a flag
# only `codex exec` takes fails a journey here instead of every supervisor turn on the host.

#: The codex release the lists below were read from, by `codex app-server --help`, and re-read
#: by hand, with its refusals, on the codex bump that moves this.
APP_SERVER_RELEASE = "codex-cli 0.159.2"
#: Its options that take a value, as the next word or after `=`, each to the value name clap
#: prints for it.
APP_SERVER_VALUED_OPTIONS = {
    "--config": "key=value",
    "--enable": "FEATURE",
    "--disable": "FEATURE",
    "--code-mode-host": "URL",
    "--listen": "URL",
    "--ws-auth": "MODE",
    "--ws-token-file": "PATH",
    "--ws-token-sha256": "HEX",
    "--ws-shared-secret-file": "PATH",
    "--ws-issuer": "ISSUER",
    "--ws-audience": "AUDIENCE",
    "--ws-max-clock-skew-seconds": "SECONDS",
}
#: Its short options, each to the long one it spells.
APP_SERVER_SHORT_OPTIONS = {"-c": "--config", "-h": "--help"}
#: Its options that take none.
APP_SERVER_FLAGS = frozenset(
    ("--strict-config", "--stdio", "--analytics-default-enabled", "--help")
)
#: Its subcommands, which this stand-in accepts as codex does and does not serve.
APP_SERVER_COMMANDS = frozenset(("daemon", "proxy", "generate-ts", "generate-json-schema", "help"))
#: The usage block clap prints under an `app-server` refusal.
APP_SERVER_USAGE = (
    "\nUsage: codex app-server [OPTIONS] [COMMAND]\n\nFor more information, try '--help'."
)
# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]

#: Set to anything non-empty, an accepted `app-server` launch is served as Codex serves one:
#: JSON-RPC on stdio, each turn answered from `FAKE_CODEX_ANSWERS` and recorded in
#: `FAKE_CODEX_ATTEMPT_LOG` and `FAKE_CODEX_PROMPT_LOG` as an `exec` turn is. Unset, an
#: accepted launch is answered as any other launch is, which is what every journey written
#: before this existed relies on. Opt-in rather than always, because a served `app-server`
#: lives until its caller closes stdin, as Codex's does, which an older journey never does.
APP_SERVER_ENV = "FAKE_CODEX_SERVES_APP_SERVER"
#: Names a file each refused `app-server` launch appends one `RefusedLaunch` line to: the
#: refusal's stderr reaches only the caller, and a journey reads why its turn never started.
APP_SERVER_REFUSAL_LOG_ENV = "FAKE_CODEX_REFUSAL_LOG"


class RefusedLaunch(TypedDict):
    """One `app-server` launch refused before it started, as the refusal log holds it."""

    argv: list[str]
    stderr: str


#: The transport this stand-in serves: stdio, a per-dispatch server. A shared server listens
#: on a socket, which it does not stand in for, and says so rather than never listening.
APP_SERVER_STDIO = ("stdio://",)
#: What this stand-in answers `--version` with; a served launch writes its release into the
#: model cache as Codex does, which is how oneharness's catalog refresh accepts the cache.
VERSION = "codex-cli 0.0.0 (fake_codex)"
#: The file Codex writes the model catalog it fetched on start into, in its home.
MODELS_CACHE = "models_cache.json"


#: What names a JSON-RPC request, and its response: a client sends a number or a string.
type RequestId = int | str
#: A thread the server holds, a turn on it, and an item of that turn.
ThreadId = NewType("ThreadId", str)
TurnId = NewType("TurnId", str)
ItemId = NewType("ItemId", str)
#: The thread every served launch opens: one per process, as one dispatch's server holds.
APP_SERVER_THREAD = ThreadId("fake-codex-thread")


class ModelEntry(TypedDict):
    """One model of the catalog Codex caches."""

    slug: str


class ModelsCache(TypedDict):
    """Codex's `models_cache.json`, in the three fields oneharness's catalog refresh reads."""

    fetched_at: str
    client_version: str
    models: list[ModelEntry]


class InputItem(TypedDict):
    """One item of a `turn/start`'s input: the prompt's text."""

    type: str
    text: str


class Request(TypedDict):
    """One JSON-RPC request a client sends, in the fields this stand-in reads."""

    id: RequestId
    method: str
    #: `threadId`, `model` and `input` are the only members read; `input` holds `InputItem`s.
    params: dict[str, object]


class ThreadRef(TypedDict):
    id: ThreadId


class TurnRef(TypedDict):
    id: TurnId
    status: NotRequired[Literal["inProgress", "completed"]]


class InitializeResult(TypedDict):
    userAgent: str


class OpenedThread(TypedDict):
    """`thread/start`'s and `thread/resume`'s result."""

    thread: ThreadRef
    model: str


class StartedTurn(TypedDict):
    """`turn/start`'s result."""

    turn: TurnRef


class ThreadPage(TypedDict):
    """`thread/list`'s and `thread/loaded/list`'s result: no thread to page through."""

    data: list[str]
    nextCursor: None


class ReadThread(TypedDict):
    """`thread/read`'s result."""

    thread: ThreadRef


class Acknowledged(TypedDict):
    """The empty result of `turn/interrupt` and `thread/unsubscribe`."""


Result = InitializeResult | OpenedThread | StartedTurn | ThreadPage | ReadThread | Acknowledged


class RpcError(TypedDict):
    code: int
    message: str


class Response(TypedDict):
    """The answer to one `Request`: its result, or the error refusing it."""

    id: RequestId
    result: NotRequired[Result]
    error: NotRequired[RpcError]


class ServedMessage(TypedDict):
    type: Literal["agentMessage"]
    id: ItemId
    text: str


class TokenBreakdown(TypedDict):
    totalTokens: int
    inputTokens: int
    cachedInputTokens: int
    outputTokens: int
    reasoningOutputTokens: int


class TokenUsage(TypedDict):
    total: TokenBreakdown
    last: TokenBreakdown
    modelContextWindow: None


class TurnParams(TypedDict):
    """`turn/started`'s and `turn/completed`'s params."""

    threadId: ThreadId
    turn: TurnRef


class DeltaParams(TypedDict):
    threadId: ThreadId
    turnId: TurnId
    itemId: ItemId
    delta: str


class ItemParams(TypedDict):
    threadId: ThreadId
    turnId: TurnId
    item: ServedMessage


class UsageParams(TypedDict):
    threadId: ThreadId
    turnId: TurnId
    tokenUsage: TokenUsage


class Notification(TypedDict):
    """One notification of an answered turn, which names no request."""

    method: Literal[
        "turn/started",
        "item/agentMessage/delta",
        "item/completed",
        "thread/tokenUsage/updated",
        "turn/completed",
    ]
    params: TurnParams | DeltaParams | ItemParams | UsageParams


def read_request(line: str) -> Request | None:
    """The request one line carries, or None for a notification or a line that is none."""
    try:
        frame = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(frame, dict):
        return None
    ask, method, params = frame.get("id"), frame.get("method"), frame.get("params")
    if not isinstance(ask, int | str) or not isinstance(method, str):
        return None  # A notification, such as `initialized`, which nothing answers.
    return Request(id=ask, method=method, params=params if isinstance(params, dict) else {})


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] clap's refusal wording, read off the same pinned release as the option list above and re-taken by hand with it on a codex bump, by the manager's ruling (c3.0) that no suite gate runs the host's installed codex; the task makes the comparison a one-off quoted in its report.  # noqa: E501 - a directive is one line
def app_server_shown(option: str) -> str:
    """How clap names an option in a refusal: its long spelling, and its value name if any."""
    value = APP_SERVER_VALUED_OPTIONS.get(option)
    return f"{option} <{value}>" if value else option


def app_server_usage(used: list[str]) -> str:
    """The usage block under an unexpected-argument refusal, naming the options used before it.

    clap lists each option the launch had already used, in the order first used, in place of
    the bare `[OPTIONS] [COMMAND]`.
    """
    if not used:
        return APP_SERVER_USAGE
    shown = " ".join(app_server_shown(option) for option in dict.fromkeys(used))
    return f"\nUsage: codex app-server {shown}\n\nFor more information, try '--help'."


def app_server_refusal(arguments: list[str]) -> str | None:
    """What codex prints refusing ``codex app-server <arguments>``, or None when it accepts.

    clap's own wording for each refusal codex makes, read off `APP_SERVER_RELEASE`: an
    unknown option, an unknown subcommand, and an option missing its value. Parsing stops at
    a subcommand, whose own arguments are its parser's. What codex checks of a value — a
    `--listen` URL, a repeated flag — is not restated: no launch this repository builds
    carries one it refuses.
    """
    used: list[str] = []
    words = iter(arguments)
    for word in words:
        if word == "--" or word in APP_SERVER_COMMANDS:
            return None
        if not word.startswith("-") or word == "-":
            return f"error: unrecognized subcommand '{word}'\n{APP_SERVER_USAGE}"
        if word.startswith("-c") and len(word) > 2:
            used.append("--config")  # `-ckey=value`: the short option with its value attached.
            continue
        name, attached, _ = word.partition("=")
        spelled = APP_SERVER_SHORT_OPTIONS.get(name, name)
        if spelled in APP_SERVER_VALUED_OPTIONS:
            if not attached and next(words, None) is None:
                return (
                    f"error: a value is required for '{app_server_shown(spelled)}' but none was "
                    "supplied\n\nFor more information, try '--help'."
                )
            used.append(spelled)
            continue
        if spelled in APP_SERVER_FLAGS and not attached:
            used.append(spelled)
            continue
        return f"error: unexpected argument '{name}' found\n{app_server_usage(used)}"
    return None


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def app_server_listens_elsewhere(arguments: list[str]) -> str | None:
    """The transport a launch asks for when it is not stdio, or None when it is stdio."""
    for at, word in enumerate(arguments):
        if word == "--listen" and at + 1 < len(arguments):
            target = arguments[at + 1]
        elif word.startswith("--listen="):
            target = word.removeprefix("--listen=")
        else:
            continue
        if target not in APP_SERVER_STDIO:
            return target
    return None


def write_models_cache(arguments: list[str]) -> None:
    """Write the catalog Codex fetches on start into its home, unless handed a catalog file.

    oneharness refreshes its shared catalog by starting one short `app-server` and waiting
    for this file, so a server that never wrote it would hold every refresh to its deadline.
    """
    home = os.environ.get("CODEX_HOME")
    if not home or any("model_catalog_json=" in word for word in arguments):
        return
    fetched = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds")
    cache = ModelsCache(
        fetched_at=fetched.replace("+00:00", "Z"),
        client_version=VERSION.split()[1],
        models=[ModelEntry(slug="fake-codex-model")],
    )
    (Path(home) / MODELS_CACHE).write_text(json.dumps(cache), encoding="utf-8")


def send(frame: Response | Notification) -> None:
    """Write one JSON-RPC frame to the client, a line apiece as Codex's stdio transport does."""
    print(json.dumps({"jsonrpc": "2.0", **frame}), flush=True)


def turn_frames(thread: ThreadId, turn: TurnId, text: str) -> list[Notification]:
    """The notifications one answered turn emits, in the order Codex emits them."""
    item = ItemId(f"{turn}-message")
    tokens = TokenBreakdown(
        totalTokens=TURN_USAGE["input_tokens"] + TURN_USAGE["output_tokens"],
        inputTokens=TURN_USAGE["input_tokens"],
        cachedInputTokens=TURN_USAGE["cached_input_tokens"],
        outputTokens=TURN_USAGE["output_tokens"],
        reasoningOutputTokens=0,
    )
    usage = TokenUsage(total=tokens, last=tokens, modelContextWindow=None)
    message = ServedMessage(type="agentMessage", id=item, text=text)
    return [
        Notification(method="turn/started", params=TurnParams(threadId=thread, turn={"id": turn})),
        Notification(
            method="item/agentMessage/delta",
            params=DeltaParams(threadId=thread, turnId=turn, itemId=item, delta=text),
        ),
        Notification(
            method="item/completed",
            params=ItemParams(threadId=thread, turnId=turn, item=message),
        ),
        Notification(
            method="thread/tokenUsage/updated",
            params=UsageParams(threadId=thread, turnId=turn, tokenUsage=usage),
        ),
        Notification(
            method="turn/completed",
            params=TurnParams(threadId=thread, turn={"id": turn, "status": "completed"}),
        ),
    ]


def turn_input(params: dict[str, object]) -> str:
    """The text of a `turn/start`'s `InputItem`s, which is the prompt the turn was given."""
    items = params.get("input")
    if not isinstance(items, list):
        return ""
    return "\n".join(
        str(item["text"]) for item in items if isinstance(item, dict) and "text" in item
    )


def respond(request: Request, thread: ThreadId) -> tuple[Response, ThreadId]:
    """The response to one request, and the thread the server holds after it.

    `turn/start` is answered by `serve_stdio`, which emits the turn's notifications after it.
    """
    ask, params = request["id"], request["params"]
    match request["method"]:
        case "initialize":
            return Response(id=ask, result=InitializeResult(userAgent="fake_codex")), thread
        case "thread/start" | "thread/resume" as opening:
            if opening == "thread/resume":
                named = params.get("threadId")
                if not isinstance(named, str) or not named:
                    refused = RpcError(code=-32602, message="threadId is required")
                    return Response(id=ask, error=refused), thread
                thread = ThreadId(named)
            model = str(params.get("model") or "fake-codex-model")
            return Response(id=ask, result=OpenedThread(thread={"id": thread}, model=model)), thread
        case "turn/interrupt" | "thread/unsubscribe":
            return Response(id=ask, result=Acknowledged()), thread
        case "thread/list" | "thread/loaded/list":
            return Response(id=ask, result=ThreadPage(data=[], nextCursor=None)), thread
        case "thread/read":
            read = ReadThread(thread={"id": ThreadId(str(params.get("threadId") or thread))})
            return Response(id=ask, result=read), thread
        case method:
            unknown = RpcError(code=-32601, message=f"unknown method {method}")
            return Response(id=ask, error=unknown), thread


def serve_stdio(argv: list[str]) -> int:
    """Answer JSON-RPC requests on stdio until the client closes it, as `codex app-server` does."""
    thread = APP_SERVER_THREAD
    turns = 0
    for line in sys.stdin:
        request = read_request(line)
        if request is None:
            continue
        if request["method"] != "turn/start":
            response, thread = respond(request, thread)
            send(response)
            continue
        turns += 1
        turn = TurnId(f"{thread}-turn-{turns}")
        record_prompt(turn_input(request["params"]), argv, [])
        text = answer(record_launch())
        send(
            Response(
                id=request["id"], result=StartedTurn(turn={"id": turn, "status": "inProgress"})
            )
        )
        for notification in turn_frames(thread, turn, text):
            send(notification)
    return 0


def serve_app_server(arguments: list[str]) -> int | None:
    """Refuse ``codex app-server <arguments>`` as codex does, or serve it when asked to.

    None when the launch is accepted and no journey asked for a served one, for the caller
    to answer it as any other launch.
    """
    refusal = app_server_refusal(arguments)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        if log := os.environ.get(APP_SERVER_REFUSAL_LOG_ENV):
            refused = RefusedLaunch(argv=["app-server", *arguments], stderr=refusal)
            with Path(log).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(refused) + "\n")
        return 2
    if not os.environ.get(APP_SERVER_ENV):
        return None
    if command := APP_SERVER_COMMANDS.intersection(arguments):
        print(f"fake_codex: stands in for no `app-server {min(command)}`", file=sys.stderr)
        return 1
    elsewhere = app_server_listens_elsewhere(arguments)
    if elsewhere is not None:
        print(f"fake_codex: serves app-server on stdio only, not {elsewhere}", file=sys.stderr)
        return 1
    write_models_cache(arguments)
    return serve_stdio(["app-server", *arguments])


def answer(launches: int | None) -> str:
    """The text this launch returns, from the scripted answers if a journey set any.

    `launches` is 1-based and None when nothing is counting them, which is the same
    case as an unscripted run: there is exactly one answer to give.
    """
    scripted = os.environ.get("FAKE_CODEX_ANSWERS")
    if not scripted:
        return DEFAULT_ANSWER
    answers = json.loads(scripted)
    if not answers:
        return DEFAULT_ANSWER
    # Past the end the last answer repeats, so a journey scripts only the launches
    # whose answers differ and lets the settled one stand for every later attempt.
    return str(answers[min((launches or 1) - 1, len(answers) - 1)])


def turn_events(text: str, *, billed: bool) -> tuple[TurnEvent, ...]:
    """One complete codex-shaped turn carrying that answer.

    Token accounting is not decoration here: a record persisted without it carries
    no evidence the turn was ever billed, which is what `billed=False` produces.
    """
    completed = TurnEvent(type="turn.completed")
    if billed:
        completed["usage"] = TURN_USAGE
    return (
        TurnEvent(type="turn.started"),
        TurnEvent(type="thread.started", thread_id="fake-codex-thread"),
        TurnEvent(type="item.completed", item=AgentMessage(type="agent_message", text=text)),
        completed,
    )


def record_launch() -> int | None:
    """Append this launch to the attempt log, returning how many it now holds."""
    log = os.environ.get("FAKE_CODEX_ATTEMPT_LOG")
    if log is None:
        return None
    path = Path(log)
    with path.open("a", encoding="utf-8") as stream:
        stream.write("launch\n")
    return len(path.read_text(encoding="utf-8").splitlines())


#: The codex options oneharness passes that take a value as the next word. A role's own
#: `[harness.codex] args` are appended *after* the prompt — `exec --json <prompt> -c
#: features.apps=false` — and codex accepts an option on either side of its positional,
#: so the prompt is the last word that is neither an option nor an option's value.
#: `tests/e2e/launch_monitor_observer/test_fake_codex_reads_each_roles_prompt_e2e.py` holds
#: this set to the argv the pinned oneharness builds for every role here, so an option it
#: starts passing fails there.
VALUED_OPTIONS = frozenset(
    (*CONFIG_OPTIONS, "-m", "--model", "-s", "--sandbox", "-C", "--cd", "-p", "--profile")
)


def read_prompt(argv: list[str]) -> str | None:
    """The prompt this launch was given, or None when a journey asked for no reading of it.

    codex takes its prompt as the last positional word of `exec --json <prompt>`,
    which is the argv oneharness builds and
    `tests/e2e/launch_monitor_observer/test_orchestrate_launch_e2e.py` reads back, followed
    by any `args` the role's config appends; each option in `VALUED_OPTIONS` is skipped with
    its value. The one other spelling codex accepts is `-`, which says the prompt is on
    stdin — what oneharness hands over when a prompt outgrows a command line, as a plan
    reviewed whole does — so that word is read through rather than taken as the prompt. Read
    once, because stdin can be read once, and always, because an llmlint judge batch is told
    apart by what it asks.
    """
    if not argv:
        return None
    positional: list[str] = []
    words = iter(argv)
    for word in words:
        if word in VALUED_OPTIONS:
            next(words, None)
        elif word == "-" or not word.startswith("-"):
            positional.append(word)
    if not positional:
        return None
    return sys.stdin.read() if positional[-1] == "-" else positional[-1]


def record_prompt(
    prompt: str | None, argv: list[str], read: list[str], *, log: str | None = None
) -> None:
    """Append the prompt this launch was given, its argv and what it opened, when asked.

    Into ``log`` when one is named, and otherwise into ``FAKE_CODEX_PROMPT_LOG``.
    """
    log = log if log is not None else os.environ.get("FAKE_CODEX_PROMPT_LOG")
    if log is None or prompt is None:
        return
    with Path(log).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(PromptRecord(prompt=prompt, argv=argv, read=read)) + "\n")


#: How a plan-level prompt lists each node and the task file it may open.
NODE_HEADING = "### Node `"
TASK_FILE = "task file: "


def read_listed(prompt: str | None) -> tuple[list[str], list[Finding]]:
    """Open each task file ``prompt`` lists, and quote every line carrying the marker.

    Answers the paths opened, in the order listed, and one finding per marked line, naming
    the node the path was listed under. Nothing is opened unless a journey named a marker.
    """
    marker = os.environ.get("FAKE_CODEX_READ_LISTED_FOR")
    if not marker or prompt is None:
        return [], []
    read: list[str] = []
    findings: list[Finding] = []
    node = ""
    for line in prompt.splitlines():
        if line.startswith(NODE_HEADING):
            node = line.removeprefix(NODE_HEADING).split("`", 1)[0]
        elif line.startswith(TASK_FILE):
            path = Path(line.removeprefix(TASK_FILE).strip())
            if not path.is_absolute() or not path.is_file():
                continue
            read.append(str(path))
            for held in path.read_text(encoding="utf-8").splitlines():
                if marker in held:
                    findings.append(
                        {"criterion": node, "why": f"its task file reads: {held.strip()}"}
                    )
    return read, findings


def run_on_marker(argv: list[str], prompt: str | None) -> None:
    """Run the commands a turn whose prompt carries a scripted marker would have run.

    Where the turn runs: the directory codex is told with `--cd`/`-C` when oneharness names
    one, and this process's own working directory otherwise. A command that fails is
    reported on stderr and the turn still answers, because what a journey reads is what
    the commands did, never this turn's exit status.
    """
    instruction = os.environ.get("FAKE_CODEX_RUN_ON_MARKER")
    if not instruction or prompt is None:
        return
    keyed = json.loads(Path(instruction).read_text(encoding="utf-8"))
    cwd = next(
        (argv[at + 1] for at, word in enumerate(argv[:-1]) if word in ("-C", "--cd")),
        None,
    )
    for marker, commands in keyed.items():
        if marker not in prompt:
            continue
        for command in commands:
            ran = subprocess.run(  # noqa: S603 - a real program, where the turn runs it
                command,
                cwd=cwd,
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            if ran.returncode != 0:
                print(
                    f"fake_codex: {command} exited {ran.returncode}\n{ran.stdout}{ran.stderr}",
                    file=sys.stderr,
                )
            if log := os.environ.get("FAKE_CODEX_RUN_ON_MARKER_LOG"):
                with Path(log).open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "command": command,
                                "cwd": cwd or os.getcwd(),
                                "status": ran.returncode,
                                "stdout": ran.stdout,
                                "stderr": ran.stderr,
                            }
                        )
                        + "\n"
                    )


def turn(launches: int | None, findings: list[Finding]) -> tuple[TurnEvent, ...]:
    """The stream this launch emits, with token accounting withheld on request.

    A launch that found marked lines in the files it opened refuses quoting them, which
    is the one answer its reading decides; every other launch answers as scripted.
    """
    text = json.dumps({"passes": False, "findings": findings}) if findings else answer(launches)
    return turn_events(text, billed=os.environ.get("FAKE_CODEX_OMIT_USAGE") != "1")


def hold() -> None:
    """Keep this turn in flight for as long as a journey asked, after recording it.

    Until the release file a journey names exists, when it names one, and for the hold's
    seconds at most either way.
    """
    seconds = float(os.environ.get("FAKE_CODEX_HOLD_SECONDS") or "0")
    if seconds <= 0:
        return
    holding = os.environ.get("FAKE_CODEX_HOLDING")
    if holding:
        Path(holding).write_text(f"{os.getpid()}\n", encoding="utf-8")
    release = os.environ.get("FAKE_CODEX_HOLD_RELEASE")
    if not release:
        time.sleep(seconds)
        return
    ends = time.monotonic() + seconds
    while not Path(release).exists() and time.monotonic() < ends:
        time.sleep(0.1)


#: The sections of llmlint's judge prompt that say a launch is one of its batches, and the
#: lines that name a batch's files and each rule's scope.
LLMLINT_TARGETS = "## Target files"
LLMLINT_RULES = "## Rules to evaluate"
LLMLINT_RULE = "### "
LLMLINT_SCOPE = "Scope: "
LLMLINT_ALL = "all target files"
LLMLINT_EXCEPT = "all target files except: "
LLMLINT_RELEVANCE = "Relevant only when:"


class BatchRule(NamedTuple):
    """One rule of an llmlint batch: where it applies and whether it asks for relevance.

    ``conditional`` is whether the rule carries a relevance condition, which decides
    whether llmlint's schema asks for a ``relevant`` field on its answer.
    """

    scope: str
    conditional: bool


class Batch(NamedTuple):
    """One llmlint judge batch: the files it names and each rule it asks about."""

    targets: list[str]
    rules: dict[str, BatchRule]


class Violation(TypedDict):
    """One violation of a rule, in the fields llmlint's schema asks for."""

    file: str
    line: int
    message: str


class RuleVerdict(TypedDict):
    """One rule's answer in llmlint's batch schema; `relevant` only for a conditional rule."""

    name: str
    rationale: str
    relevant: NotRequired[bool]
    holds: bool
    violations: list[Violation]


def llmlint_batch(prompt: str | None) -> Batch | None:
    """The files and rules of an llmlint judge batch, or None when ``prompt`` is not one."""
    if prompt is None or LLMLINT_TARGETS not in prompt or LLMLINT_RULES not in prompt:
        return None
    targets_part = prompt.split(LLMLINT_TARGETS, 1)[1].split(LLMLINT_RULES, 1)[0]
    targets = [
        line.removeprefix("- ").strip()
        for line in targets_part.splitlines()
        if line.startswith("- ")
    ]
    rules: dict[str, BatchRule] = {}
    name = ""
    for line in prompt.split(LLMLINT_RULES, 1)[1].splitlines():
        match line:
            case _ if line.startswith(LLMLINT_RULE):
                name = line.removeprefix(LLMLINT_RULE).strip()
                rules[name] = BatchRule(scope=LLMLINT_ALL, conditional=False)
            case _ if name and line.startswith(LLMLINT_SCOPE):
                rules[name] = rules[name]._replace(scope=line.removeprefix(LLMLINT_SCOPE).strip())
            case _ if name and line.startswith(LLMLINT_RELEVANCE):
                rules[name] = rules[name]._replace(conditional=True)
            case _ if line.startswith("## "):
                break
    return Batch(targets, rules)


def in_scope(path: str, scope: str, targets: list[str]) -> bool:
    """Whether a rule whose ``Scope:`` line reads ``scope`` applies to ``path``."""
    if path not in targets:
        return False
    if scope == LLMLINT_ALL:
        return True
    if scope.startswith(LLMLINT_EXCEPT):
        return path not in [part.strip() for part in scope.removeprefix(LLMLINT_EXCEPT).split(",")]
    return path in [part.strip() for part in scope.split(",")]


def llmlint_verdict(batch: Batch) -> str:
    """The answer to one llmlint batch: each rule holds unless scripted to fail in scope."""
    failing = json.loads(os.environ.get("FAKE_CODEX_LLMLINT_FAIL") or "{}")
    omitted = json.loads(os.environ.get("FAKE_CODEX_LLMLINT_OMIT") or "[]")
    answered: dict[str, RuleVerdict] = {}
    for name, rule in batch.rules.items():
        if name in omitted or "*" in omitted:
            continue
        scripted = failing.get(name)
        each = [scripted] if isinstance(scripted, dict) else scripted or []
        violations: list[Violation] = [
            Violation(
                file=str(one["file"]),
                line=int(one.get("line", 1)),
                message=str(one["message"]),
            )
            for one in each
            if isinstance(one, dict) and in_scope(str(one["file"]), rule.scope, batch.targets)
        ]
        verdict = RuleVerdict(
            name=name,
            rationale=f"fake_codex: {name} {'fails' if violations else 'holds'} as scripted",
            holds=not violations,
            violations=violations,
        )
        if rule.conditional:
            verdict["relevant"] = True
        answered[name] = verdict
    return json.dumps(answered)


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        # The oneharness-core the 0.5.14 oneagentgraph links probes the binary's version
        # before a turn. A probe is not a launch: counting it would shift every scripted
        # answer and `FAKE_CODEX_UNAVAILABLE_ATTEMPTS` onto the wrong turn.
        print(VERSION)
        return 0
    if sys.argv[1:2] == ["app-server"]:
        served = serve_app_server(sys.argv[2:])
        if served is not None:
            return served
    prompt = read_prompt(sys.argv[1:])
    batch = llmlint_batch(prompt)
    if batch is not None:
        # Recorded only where a journey asks for llmlint's batches, and never beside the
        # reviewer's turns, which journeys read back in order.
        if llmlint_log := os.environ.get("FAKE_CODEX_LLMLINT_PROMPT_LOG"):
            record_prompt(prompt, sys.argv[1:], [], log=llmlint_log)
        if left_log := os.environ.get("FAKE_CODEX_LLMLINT_LEAVE_TEMPORARY"):
            left = (
                Path(tempfile.gettempdir()) / f"codex-bwrap-synthetic-mount-targets-{os.getpid()}"
            )
            left.mkdir()
            with open(left_log, "a", encoding="utf-8") as stream:
                stream.write(f"{left}\n")
        for event in turn_events(llmlint_verdict(batch), billed=True):
            print(json.dumps(event), flush=True)
        return 0
    read, findings = read_listed(prompt)
    record_prompt(prompt, sys.argv[1:], read)
    launches = record_launch()
    run_on_marker(sys.argv[1:], prompt)
    hold()
    unavailable = int(os.environ.get("FAKE_CODEX_UNAVAILABLE_ATTEMPTS") or "0")
    if launches is not None and launches <= unavailable:
        print("fake_codex: the provider started and then failed", file=sys.stderr)
        return 1
    for event in turn(launches, findings):
        print(json.dumps(event), flush=True)
    if os.environ.get("FAKE_CODEX_FAIL_AFTER_TURN") == "1":
        # The turn above is complete and accounted for, so this exit is a failure
        # with work behind it — the case a chain must never spend another
        # identity's quota on, whatever the exit code fails to explain.
        print("fake_codex: the provider answered and then failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
