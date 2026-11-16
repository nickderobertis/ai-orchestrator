"""The dispatch judge's one grant, held on the real harness CLIs it runs.

`oneharness.judge.toml` runs every judge turn `read-only` with one run-level grant,
`allowed_tools = ["Bash($ORCHESTRATOR_DECLINE_RULING:*)"]`, which the pinned `oneharness`
resolves to the decline wrapper's absolute path and applies to that run alone: on Claude Code
as per-run flags, on Codex as a per-run overlay home whose `rules/` allows the path outside the
read-only sandbox. `tests/e2e/launch_monitor_observer/test_supervisory_codex_posture_e2e.py`
reads the argv that builds; this module proves what it does.

For each identity of the judge chain whose harness the pinned release grants, one judge turn
runs through the real `oneharness run --config oneharness.judge.toml --harness <identity>`,
the real harness CLI the identity names, and that CLI's own permission check and sandbox. Only
the model is doubled: a scripted endpoint this module serves, which each CLI is pointed at
through its environment — Claude Code by `ANTHROPIC_BASE_URL` and a scratch config directory,
Codex by a scratch home whose `config.toml` names the endpoint as its model provider, the way
the `spike-command-grant` probe pointed them — never through a tracked judge config. The
scripted model reads the decline command's absolute path out of the system instructions the
CLI sent it, then asks for two tool calls: a `touch` of a new file in the worktree, and that
path with `--ruling <ref> --reason '<multi-line reason>'`, where the path is this checkout's
real `scripts/decline-ruling.sh` and the ruling is a manager's note on a run held at a gate.
That run is launched, noted and read through the pinned engine's own verbs — `onepipeline
start`, `reply` and `next` — over a plan store and node graph of its own, so nothing of this
checkout's recipes, graphs or launch configuration is under test here.

A turn passes only when the CLI handed back a decision for each call, the worktree is
byte-identical afterwards, the run's channel holds one `declined-ruling` surface carrying the
reason, and nothing the overlay made is left in the worktree, the identity's harness home or
oneharness's state directory.

Every harness home, state directory and `HOME` is this module's own scratch, so no real
login, rule file or session store is read or written; the endpoint answers every request, so
no turn reaches a paid provider.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import subprocess
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import short_state
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The pinned engine, as the lock installs it: the one `scripts/decline-ruling.sh` execs.
ONEPIPELINE = REPO_ROOT / "node_modules" / ".bin" / "onepipeline"

#: The config under test, and the wrapper its grant names through the variable.
JUDGE_CONFIG = REPO_ROOT / "oneharness.judge.toml"
DECLINE_WRAPPER = REPO_ROOT / "scripts" / "decline-ruling.sh"
DECLINE_VARIABLE = "ORCHESTRATOR_DECLINE_RULING"

#: The pinned CLI the lock installs beside the interpreter running this suite.
ONEHARNESS = Path(os.environ.get("VIRTUAL_ENV", REPO_ROOT / ".venv")) / "bin" / "oneharness"

#: How oneharness states a grant in a turn's system instructions: each granted path in
#: backticks after this opening. Its wording is oneharness's; that each path appears is the
#: contract (`domain::grant::statement`).
GRANT_STATEMENT = re.compile(r"This run may execute these commands: `(?P<path>/[^`]+)`")

#: The marker each turn's prompt carries, so the endpoint finds that turn's script.
MARK = re.compile(r"\[judge-grant:(?P<id>[A-Za-z0-9_-]+)\]")

#: The reason each judge declines with, several lines and no apostrophe, so one
#: single-quoted argument carries it, as the appendix tells a judge to pass it.
REASON = (
    "The criteria hold the change to its failure path.\n"
    "Dropping that test would hide a regression from the user."
)

#: The ruling every turn declines: a manager's note on the held run.
MANAGER_NOTE = "Drop the failure-path test; the happy path is enough."

#: The node behind the held run's gate, which every note is addressed to. Never dispatched.
WORK_NODE = "work"

#: The new file the first tool call tries to create in the worktree.
INTRUDER = "written-by-the-judge.txt"

#: What the held run's launch must not inherit from an enclosing dispatch, and what each turn
#: must not inherit from this host's own harness routing and logins.
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ORCHESTRATOR_ASK_MANAGER",
    DECLINE_VARIABLE,
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)
HOST_ROUTING = ("ONEHARNESS_", "CODEX_", "CLAUDE_", "ANTHROPIC_", "OPENAI_", "ORCHESTRATOR_")

#: The plan store the held run is read from: one local source in the run's own directory,
#: and nothing of this host's sources, user layer or default selection.
PLAN_STORE = (
    "default_sources: [held]\n"
    "sources:\n  held:\n    plugin: local-md\n    config:\n      root: store\n"
)

#: The node-scope graph the engine reads at launch. The held node is never dispatched, so
#: its one member is never started; this checkout's own graphs are not this test's subject.
NODE_GRAPH = "version: 1\nname: node-scope\nmembers:\n  worker:\n    kind: oneharness\n"

#: The variable each identity reads its harness home from, as `oneharness.identities.toml`
#: maps it; `None` where the identity uses the harness's own default variable.
HOME_VARIABLE = {
    "codex:primary": None,
    "codex:alternate": "ORCHESTRATOR_CODEX_ALT_HOME",
    "claude-code:alternate": "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR",
    "claude-code:alternate2": "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR",
    "claude-code:alternate3": "ORCHESTRATOR_CLAUDE_ALT3_CONFIG_DIR",
    "claude-code:primary-backup": "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR",
    "claude-code:primary": "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR",
}


def _run_json(*arguments: str) -> dict[str, Any]:
    """One read-only verb of the pinned CLI, as JSON."""
    shown = subprocess.run(
        [str(ONEHARNESS), *arguments, "--format", "json"],
        cwd=REPO_ROOT,
        env={k: v for k, v in os.environ.items() if not k.startswith("ONEHARNESS_")},
        capture_output=True,
        text=True,
        timeout=e2e_timeout(60),
        check=True,
    )
    return cast(dict[str, Any], json.loads(shown.stdout))


def _granted_identities() -> list[str]:
    """Every identity of the judge chain whose harness the pinned release grants here."""
    chain: list[str] = _run_json("config", "--config", str(JUDGE_CONFIG))["harnesses"]["value"]
    listed = {entry["id"]: entry for entry in _run_json("list")["harnesses"]}
    return [
        identity
        for identity in chain
        if listed[identity.partition(":")[0]]["supports_run_allowed_tools"] is True
    ]


@dataclass
class Script:
    """One turn's script, and what the endpoint saw of it."""

    worktree: Path
    ruling: str
    path: str | None = None
    calls: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    requests: int = 0


class Endpoint:
    """The scripted model, serving Anthropic Messages and OpenAI Responses on loopback."""

    def __init__(self) -> None:
        self.scripts: dict[str, Script] = {}
        self.lock = threading.Lock()
        handler = type("Bound", (_Handler,), {"endpoint": self})
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.port = int(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _text(content: object) -> str:
    """The text of a message's content, however the dialect nests it."""
    match content:
        case str():
            return content
        case list():
            return "\n".join(_text(part) for part in content)
        case dict():
            return "\n".join(
                _text(content[key]) for key in ("text", "content", "output") if key in content
            )
        case _:
            return ""


def _instructions(dialect: str, body: dict[str, Any]) -> str:
    """The instructions the CLI sent beside the turn's prompt, where each CLI puts the system
    text oneharness maps for it: Claude Code's `system`; for `codex exec`, which takes no
    system argument, oneharness's mapping prefixes it to the turn's own message, so every
    input item is read. The prompt this module writes names no path, so a path found here is
    one the CLI was handed by oneharness."""
    if dialect == "anthropic":
        return _text(body.get("system"))
    parts = [_text(body.get("instructions"))]
    parts += [
        _text(item.get("content")) for item in body.get("input") or [] if isinstance(item, dict)
    ]
    return "\n".join(parts)


def _offered(dialect: str, body: dict[str, Any]) -> set[str]:
    """The tool names a request offers the model, Codex's code-mode namespaces included."""
    if dialect == "anthropic":
        return {str(tool.get("name")) for tool in body.get("tools") or [] if isinstance(tool, dict)}
    offered: set[str] = set()

    def walk(tools: object) -> None:
        for tool in tools if isinstance(tools, list) else []:
            if isinstance(tool, dict):
                offered.add(str(tool.get("name") or tool.get("type")))
                walk(tool.get("tools"))

    walk(body.get("tools"))
    for item in body.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            walk(item.get("tools"))
    return offered


class History(NamedTuple):
    """What a turn's request carries of the turn so far."""

    #: The tool calls the model has made.
    made: int
    #: The decisions the CLI handed back for them, in order.
    results: list[str]


def _history(dialect: str, body: dict[str, Any]) -> History:
    """The tool calls the model made in this turn so far, and the results handed back."""
    made, results = 0, []
    if dialect == "anthropic":
        for message in body.get("messages") or []:
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                match block:
                    case {"type": "tool_use"}:
                        made += 1
                    case {"type": "tool_result", **rest}:
                        results.append(_text(rest.get("content")))
        return History(made, results)
    for item in body.get("input") or []:
        match item:
            case {"type": "function_call" | "custom_tool_call" | "local_shell_call"}:
                made += 1
            case {"type": "function_call_output" | "custom_tool_call_output", **rest}:
                output = rest.get("output")
                results.append(output if isinstance(output, str) else json.dumps(output))
    return History(made, results)


class _Handler(http.server.BaseHTTPRequestHandler):
    endpoint: Endpoint

    def log_message(self, *_args: object) -> None:
        pass

    def _send_json(self, value: object, code: int = 200) -> None:
        raw = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_events(self, events: list[tuple[str, object]]) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for name, data in events:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802 - the stdlib handler's name
        self._send_json({"object": "list", "data": [], "models": []})

    def do_POST(self) -> None:  # noqa: N802 - the stdlib handler's name
        length = self.headers.get("content-length") or "0"
        if not (length.isdigit() and len(length) <= 9):
            self._send_json({"error": {"message": "unreadable content-length"}}, 400)
            return
        raw = self.rfile.read(int(length))
        path = self.path.split("?")[0]
        if path.endswith("/count_tokens"):
            self._send_json({"input_tokens": 10})
            return
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = None
        dialect = (
            "anthropic"
            if path.endswith("/messages")
            else "responses"
            if path.endswith("/responses")
            else None
        )
        if not isinstance(body, dict) or dialect is None:
            self._send_json({"error": {"message": f"unscripted request to {path}"}}, 404)
            return
        marks = list(MARK.finditer(json.dumps(body)))
        with self.endpoint.lock:
            script = self.endpoint.scripts.get(marks[-1]["id"]) if marks else None
        offered = _offered(dialect, body)
        if script is None or not offered:
            # A side request (a title, a summary): answered, and never scripted.
            self._answer(dialect, offered, body, None)
            return
        made, results = _history(dialect, body)
        script.requests += 1
        script.results = results
        if made == 0:
            found = GRANT_STATEMENT.search(_instructions(dialect, body))
            script.path = found["path"] if found else None
        command = self._next(script, made)
        if command is not None:
            script.calls.append(command)
        self._answer(dialect, offered, body, command)

    @staticmethod
    def _next(script: Script, made: int) -> str | None:
        """The turn's next shell command: the write, then the decline, then nothing."""
        if made == 0:
            return f"touch {script.worktree / INTRUDER}"
        if made == 1 and script.path is not None:
            return f"{script.path} --ruling {script.ruling} --reason '{REASON}'"
        return None

    def _answer(
        self, dialect: str, offered: set[str], body: dict[str, Any], command: str | None
    ) -> None:
        if dialect == "anthropic":
            self._anthropic(body, command)
        else:
            self._responses(offered, command)

    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] This is the test's own double of two provider protocols, the shape oneharness's live suite and the `spike-command-grant` probe serve; the real CLIs that read it are its drift alarm, since a CLI that stops understanding it records no decision and the turn fails here as one.  # noqa: E501 - a directive is one line
    def _anthropic(self, body: dict[str, Any], command: str | None) -> None:
        tid = "toolu_" + uuid.uuid4().hex[:20]
        if command is None:
            start: dict[str, Any] = {"type": "text", "text": ""}
            delta: dict[str, Any] = {"type": "text_delta", "text": "done"}
            final: dict[str, Any] = {"type": "text", "text": "done"}
            stop = "end_turn"
        else:
            arguments = {"command": command, "description": "judge grant probe"}
            start = {"type": "tool_use", "id": tid, "name": "Bash", "input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(arguments)}
            final = {"type": "tool_use", "id": tid, "name": "Bash", "input": arguments}
            stop = "tool_use"
        message = {
            "id": "msg_" + uuid.uuid4().hex[:20],
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "m"),
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        if not body.get("stream"):
            self._send_json({**message, "content": [final], "stop_reason": stop})
            return
        self._send_events(
            [
                ("message_start", {"type": "message_start", "message": {**message, "content": []}}),
                (
                    "content_block_start",
                    {"type": "content_block_start", "index": 0, "content_block": start},
                ),
                (
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": delta},
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                (
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": stop, "stop_sequence": None},
                        "usage": {"output_tokens": 5},
                    },
                ),
                ("message_stop", {"type": "message_stop"}),
            ]
        )

    def _responses(self, names: set[str], command: str | None) -> None:
        call_id = "call_" + uuid.uuid4().hex[:12]
        if command is None:
            item: dict[str, Any] = {
                "type": "message",
                "role": "assistant",
                "id": "msg_" + uuid.uuid4().hex[:12],
                "content": [{"type": "output_text", "text": "done", "annotations": []}],
            }
        elif "exec" in names:
            # Code mode: the shell tool is reached from the `exec` tool's JavaScript, the
            # route the spike saw a real model take to `exec_command`.
            code = (
                f"const r = await tools.exec_command({{cmd: {json.dumps(command)}}});\n"
                "text(JSON.stringify(r));"
            )
            item = {
                "type": "custom_tool_call",
                "id": "ctc_" + uuid.uuid4().hex[:12],
                "name": "exec",
                "input": code,
                "call_id": call_id,
            }
        else:
            arguments: dict[str, object]
            if "exec_command" in names:
                name, arguments = "exec_command", {"cmd": command}
            elif "shell_command" in names:
                name, arguments = "shell_command", {"command": command}
            else:
                name, arguments = "shell", {"command": ["bash", "-lc", command]}
            item = {
                "type": "function_call",
                "id": "fc_" + uuid.uuid4().hex[:12],
                "name": name,
                "arguments": json.dumps(arguments),
                "call_id": call_id,
            }
        rid = "resp_" + uuid.uuid4().hex[:20]
        usage = {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 5,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 15,
        }
        self._send_events(
            [
                ("response.created", {"type": "response.created", "response": {"id": rid}}),
                (
                    "response.output_item.added",
                    {"type": "response.output_item.added", "output_index": 0, "item": item},
                ),
                (
                    "response.output_item.done",
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                ),
                (
                    "response.completed",
                    {
                        "type": "response.completed",
                        "response": {"id": rid, "usage": usage, "output": [item]},
                    },
                ),
            ]
        )

    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


class Held(NamedTuple):
    """A launched run held at its gate, the environment its verbs need, and its directory."""

    environment: dict[str, str]
    run: str
    root: Path


@pytest.fixture(scope="module")
def endpoint() -> Iterator[Endpoint]:
    served = Endpoint()
    try:
        yield served
    finally:
        served.stop()


def _engine(held: Held, *arguments: str, stdin: str | None = None) -> str:
    """One verb of the pinned engine against the held run, from the run's own directory."""
    done = subprocess.run(
        [str(ONEPIPELINE), *arguments],
        cwd=held.root,
        env=held.environment,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert done.returncode == 0, f"onepipeline {arguments[0]}: {done.stderr}{done.stdout}"
    return done.stdout


def _write_plan(root: Path, run: str) -> None:
    """The held plan, in the local source's own layout: a human gate, and behind it the one
    node every ruling is addressed to."""
    criteria = "\n\n## Why\nHold the run.\n\n## Acceptance criteria\n- Reported.\n"
    files = {
        f"store/projects/{run}.md": (
            f"---\ntitle: {run}\nstatus: todo\nmetadata:\n  onepipeline.goal:\n"
            "    text: hold a channel open for a judge to decline a ruling on\n"
            "  onepipeline.schema_version: 3\n---\nHeld.\n"
        ),
        f"store/tasks/{run}/gate.md": (
            f'---\ntitle: "gate"\nproject: "{run}"\nstatus: "todo"\nmetadata:\n'
            '  "onepipeline.id": "gate"\n  "onepipeline.kind": "human"\n---\nApprove.\n'
        ),
        f"store/tasks/{run}/{WORK_NODE}.md": (
            f'---\ntitle: "{WORK_NODE}"\nproject: "{run}"\nstatus: "todo"\n'
            f'depends_on: ["{run}/gate"]\nmetadata:\n  "onepipeline.id": "{WORK_NODE}"\n'
            f'  "onepipeline.persona": "engineer"\n---\n## What\nReport.{criteria}'
        ),
        "onetaskgraph.yaml": PLAN_STORE,
        "graphs/node-scope.yaml": NODE_GRAPH,
    }
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    (root / "config").mkdir()


@pytest.fixture(scope="module")
def held(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Held]:
    """A run whose frontier is a human gate, launched by the pinned `onepipeline start` from a
    plan store of its own, so its channel outlives the launch and nothing is dispatched: what
    the real pinned `onepipeline decline-ruling` declines on."""
    root = tmp_path_factory.mktemp("judge-grant-run")
    run = f"judge-grant-{os.getpid()}"
    _write_plan(root, run)
    environment = {
        k: v
        for k, v in os.environ.items()
        if k not in INHERITED and not k.startswith("ONETASKGRAPH_")
    }
    environment.update(
        # Owned by a session of its own, so its own `stop` ends it.
        ONEPIPELINE_LAUNCHER="claude-code",
        ONEPIPELINE_LAUNCHER_SESSION="e2e-judge-grant",
        ONEPIPELINE_RUNS_DIR=str(root / "runs"),
        XDG_STATE_HOME=str(short_state.state_home(root)),
        # No user layer of the host's plan store reaches the run's.
        XDG_CONFIG_HOME=str(root / "config"),
    )
    launched = Held(environment, run, root)
    _engine(launched, "start", f"held:{run}", "--detach")
    try:
        yield launched
    finally:
        subprocess.run(
            [str(ONEPIPELINE), "stop", run],
            cwd=root,
            env=environment,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )


def _ruling(held: Held) -> str:
    """A manager's note to the held node, sent through `onepipeline reply`, and its reference
    (`c<envelope>.<index>`, as `tests/ask_seam/decline_ruling/` reads it off the receipt)."""
    envelope = {
        "version": 4,
        "commands": [{"op": "note", "id": WORK_NODE, "addressee": "both", "text": MANAGER_NOTE}],
    }
    receipt = cast(
        dict[str, Any], json.loads(_engine(held, "reply", held.run, stdin=json.dumps(envelope)))
    )
    assert receipt.get("state") == "applied", receipt
    return f"c{receipt['reply']}.0"


def _next_surface(held: Held) -> dict[str, Any] | None:
    """The run's next unread surface, read through `onepipeline next` as a manager reads one."""
    read = cast(dict[str, Any], json.loads(_engine(held, "next", held.run)))
    return cast(dict[str, Any] | None, read.get("surface"))


def _tree(root: Path) -> dict[str, str]:
    """Every file under `root`, by relative path, to the digest of its bytes."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _overlay_traces(root: Path, wrapper: str) -> list[str]:
    """Files under `root` an overlay would leave: its directories and its rule for the path."""
    traces = []
    for path in sorted(root.rglob("*")):
        named = str(path.relative_to(root))
        if "run-overlay" in named or "oneharness-run" in named:
            traces.append(named)
        elif path.is_file() and not path.is_symlink() and path.stat().st_size < 1_000_000:
            text = path.read_bytes().decode("utf-8", errors="replace")
            if f'prefix_rule(pattern=["{wrapper}"]' in text or f"Bash({wrapper}" in text:
                traces.append(named)
    return traces


class World(NamedTuple):
    """Where one judge turn runs: the work it judges and its identity's own harness home."""

    worktree: Path
    harness_home: Path


def _world(root: Path, identity: str, port: int) -> World:
    """A worktree and the identity's own harness home, pointed at the scripted endpoint."""
    worktree = root / "worktree"
    worktree.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    (worktree / "README.md").write_text("the work a judge reads\n", encoding="utf-8")
    home = root / "harness-home"
    (home / "sessions").mkdir(parents=True)
    if identity.startswith("codex"):
        (home / "config.toml").write_text(
            'model_provider = "scripted"\n'
            f'chatgpt_base_url = "http://127.0.0.1:{port}/backend-api/"\n'
            "[analytics]\nenabled = false\n"
            '[model_providers.scripted]\nname = "scripted"\n'
            f'base_url = "http://127.0.0.1:{port}/v1"\nwire_api = "responses"\n'
            'env_key = "CODEX_SCRIPTED_KEY"\nrequest_max_retries = 0\nstream_max_retries = 0\n',
            encoding="utf-8",
        )
    else:
        # The identity masks every Anthropic credential variable, so the scratch config
        # directory supplies the key Claude Code reads; it is never a real login.
        (home / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_API_KEY": "scripted"}}), encoding="utf-8"
        )
    return World(worktree, home)


def _environment(held: Held, root: Path, identity: str, home: Path, port: int) -> dict[str, str]:
    """The judge turn's environment: the held run's verbs, and scratch for everything a
    harness would otherwise read from this host's own logins and routing."""
    environment = {k: v for k, v in held.environment.items() if not k.startswith(HOST_ROUTING)}
    scratch = root / "node-scratch"
    scratch.mkdir()
    (root / "home").mkdir()
    environment.update(
        HOME=str(root / "home"),
        XDG_STATE_HOME=str(short_state.state_home(root / "state")),
        TMPDIR=str(root / "tmp"),
        ONEPIPELINE_RUN_ID=held.run,
        ONEPIPELINE_NODE_SCRATCH_DIR=str(scratch),
        CODEX_SCRIPTED_KEY="scripted",
        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}",
    )
    environment[DECLINE_VARIABLE] = str(DECLINE_WRAPPER)
    (root / "tmp").mkdir()
    variable = HOME_VARIABLE[identity]
    if variable is None:
        environment["CODEX_HOME"] = str(home)
    else:
        environment[variable] = str(home)
    return environment


GRANTED = _granted_identities()


def test_the_judge_chain_has_identities_the_release_grants() -> None:
    """Both of the judge chain's harnesses are ones the pinned release grants on this host,
    so the turns below cover every identity rather than none."""
    assert {identity.partition(":")[0] for identity in GRANTED} == {"codex", "claude-code"}, GRANTED


@pytest.mark.parametrize("identity", GRANTED)
def test_a_judge_turn_may_decline_and_may_not_write(
    identity: str, held: Held, endpoint: Endpoint, tmp_path: Path
) -> None:
    """One judge turn on the real CLI: the write is refused, the decline reaches the run."""
    root = Path(short_state.state_home(tmp_path)) / "w"
    world = _world(root, identity, endpoint.port)
    worktree, home = world.worktree, world.harness_home
    environment = _environment(held, root, identity, home, endpoint.port)
    ruling = _ruling(held)
    probe = uuid.uuid4().hex[:12]
    script = Script(worktree, ruling)
    with endpoint.lock:
        endpoint.scripts[probe] = script
    worktree_before = _tree(worktree)
    home_before = set(_tree(home))

    turn = subprocess.run(
        [
            str(ONEHARNESS),
            "run",
            "--config",
            str(JUDGE_CONFIG),
            "--harness",
            identity,
            "--cwd",
            str(worktree),
            "--prompt",
            f"[judge-grant:{probe}] Judge the work in this worktree.",
            "--format",
            "json",
        ],
        cwd=worktree,
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(600),
        check=False,
    )

    what = f"{identity}: exit {turn.returncode}\n{turn.stderr[-3000:]}\n{turn.stdout[-3000:]}"
    assert script.path == str(DECLINE_WRAPPER), (
        f"the CLI's system instructions named {script.path!r}, not the wrapper's absolute "
        f"path:\n{what}"
    )
    assert len(script.calls) == 2, f"the scripted model asked for {script.calls}:\n{what}"
    assert len(script.results) >= 2, (
        f"the CLI handed back {len(script.results)} decision(s) for two calls, so a call "
        f"never reached one:\n{script.results}\n{what}"
    )
    changed = sorted(set(_tree(worktree).items()) ^ set(worktree_before.items()))
    assert not changed, f"the judge turn changed the worktree: {changed}\n{script.results}"
    assert not (worktree / INTRUDER).exists()

    surface = _next_surface(held)
    assert surface is not None, f"the run's channel holds no decline:\n{script.results}\n{what}"
    assert surface["kind"] == "declined-ruling", surface
    record = cast(dict[str, Any], surface)["declined_ruling"]
    assert (record["ruling"], record["provenance"], record["reason"]) == (
        ruling,
        "manager",
        REASON,
    ), record
    assert _next_surface(held) is None, "more than one decline"

    wrapper = str(DECLINE_WRAPPER)
    leftovers = {
        "worktree": _overlay_traces(worktree, wrapper),
        "harness home": [
            name for name in _overlay_traces(home, wrapper) if name not in home_before
        ],
        "state directory": _overlay_traces(Path(environment["XDG_STATE_HOME"]), wrapper),
    }
    assert not any(leftovers.values()), f"the turn's overlay left {leftovers}"
