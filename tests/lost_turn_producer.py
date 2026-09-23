"""A turn the real producer really loses, the way a monitor loses one, offline.

The monitor's judge side — `onemessagebus serve --codec monitor`, as `graphs/dag-scope.yaml`
declares it — reads a lost turn off the `turn` object onejudge reports, and
`tests/e2e/test_lost_turn_wire_contract_e2e.py` proves the producer this host installs still
reaches it that way. Arranging the loss lives here.

Nothing about the producer is stood in for. A real `oneharness` runs the real `codex`
binary, and the only thing arranged is that the model endpoint refuses every turn it is
asked. A refused turn is the same terminal shape as the quota refusal that cost this host its
monitor for a day, and it is the half a check can produce on demand: offline, in under a
second, with no paid account and no credential of this host's in reach.

The same arrangement answers a second question about that path, which is why the model
the request names is recorded here rather than in a producer of its own: **which model a
controlled turn runs under**. Every codex-first supervisory side on this host takes
`--control`, and until oneharness's control path was handed the candidate's own model
(https://github.com/nickderobertis/oneharness/pull/1284) a `[harness.codex].model` never
reached `thread/start`, so codex ran whatever its own config named while the record said
the configured one. A refused turn is enough to see that: the request reaches the
endpoint carrying the model it would be billed under before the refusal, and the server
states the thread's model on its open response before any turn is sent.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import threading
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, TypedDict

import short_state

#: The one producer whose transcript reaches the monitor's judge side, as `oneharness list` ids it.
PRODUCER = "codex"

#: The prompt the lost turn carries. Never answered — the provider refuses it — but it is
#: what makes this a turn rather than a handshake.
LOST_PROMPT = "Report what has drifted from the plan."

#: The model endpoint the provider is pointed at, filled in with `refusing_endpoint`'s
#: own address: loopback, so the turn is refused at once and this reaches no network and
#: no paid account. `requires_openai_auth = false` with a placeholder key keeps the real
#: credential store out of it entirely. The `model` is codex's **own** default — what the
#: server runs a thread under when the request names none — which is the seat the
#: controlled-turn journey moves to another name.
REFUSING_PROVIDER = """model = "{model}"
model_provider = "refusing"

[model_providers.refusing]
name = "refusing"
base_url = "{base_url}/v1"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
"""

#: The model the codex home names by default, and what every caller of `refusing_home`
#: that names none gets.
HOME_MODEL = "gpt-5-codex"

#: The two models the misrouting was told in, spelled as codex knows them. One is what a
#: side's `[harness.codex].model` names and the record reports; the other is what
#: `~/.codex/config.toml` named as the server's default on this host, which is what a
#: controlled turn ran under while the control path carried no model at all — roughly ten
#: times the weekly quota per token, recorded as the first. Which is which is the whole
#: of what the journey reads off the wire, so both are named here rather than in it.
CONFIGURED_MODEL = "gpt-5.6-sol"
SERVER_DEFAULT_MODEL = "gpt-6-astra"

#: What the endpoint refuses with. A provider that will not serve a turn answers it — 401
#: for a credential it rejects, 429 for a quota that is spent — and the status is what
#: codex classifies, so any refusal will do and this one needs no credential to be
#: plausible.
REFUSAL_STATUS = 401
REFUSAL_BODY = (
    b'{"error": {"message": "every turn is refused here", "type": "invalid_request_error"}}'
)


class RecordedRefusals(NamedTuple):
    """A refusing endpoint that also keeps what each refused request asked for."""

    #: Where the provider stanza points.
    base_url: str
    #: The `model` each request named, in the order the requests arrived. `None` for a
    #: request naming none, and a marker for a body that was not JSON at all — never
    #: dropped, because a request that reached the endpoint is the evidence.
    models: list[str | None]


def _serve_refusals(models: list[str | None] | None) -> str:
    """Serve the refusal on an ephemeral loopback port, recording requests into `models`.

    A daemon thread serves it, so nothing needs cleanup. A closed port, which this used,
    no longer loses a turn: codex reads a refused connection as the network being down
    and reconnects past both `_max_retries` keys until `capture`'s `--timeout` expires,
    reporting a deadline instead. A status is also the truer shape, since the refusal a
    lost monitor turn is named by arrives as one rather than as a closed socket, and
    codex classifies it at once.
    """

    class Refuse(http.server.BaseHTTPRequestHandler):
        """Answer every request with the refusal, and keep the request log quiet."""

        def do_POST(self) -> None:  # noqa: N802 — the name is BaseHTTPRequestHandler's
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if models is not None:
                models.append(_model_named_by(body))
            self.send_response(REFUSAL_STATUS)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(REFUSAL_BODY)))
            self.end_headers()
            self.wfile.write(REFUSAL_BODY)

        do_GET = do_POST  # noqa: N815 — likewise

        def log_message(self, format: str, *args: object) -> None:
            """Nothing: a refused turn is the subject, and its requests are not evidence."""

    server = http.server.HTTPServer(("127.0.0.1", 0), Refuse)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


def _model_named_by(body: bytes) -> str | None:
    """The model one request to the model endpoint asked to be run under."""
    try:
        request = json.loads(body)
    except ValueError:
        return "<not a JSON request>"
    model = request.get("model") if isinstance(request, dict) else None
    return model if isinstance(model, str) else None


@lru_cache(maxsize=1)
def refusing_endpoint() -> str:
    """A local endpoint that refuses every turn, served once per process.

    Cached because the lost turn is captured once per session and shared; what its
    requests asked for is nobody's evidence, so nothing is recorded.
    """
    return _serve_refusals(None)


def recording_refusals() -> RecordedRefusals:
    """A fresh refusing endpoint whose record of requests belongs to one caller.

    Not cached, on purpose: the record is the answer, and a shared one would carry
    another journey's requests.
    """
    models: list[str | None] = []
    return RecordedRefusals(base_url=_serve_refusals(models), models=models)


#: The session name the run is addressed by. Ordinary rather than the one character it had
#: to be while the session directory sat under `tmp_path`: `--control` binds a Unix socket
#: under that directory and Linux caps the address at 108 bytes, which the short state root
#: `tests/short_state.py` mints leaves room under.
CONTROL_SESSION = "controlled-turn"

#: How long either local command may take before it is treated as hung. Both are
#: sub-second against binaries on this host, so this only ever catches a wedge — there is
#: no deadline here whose expiry is the behaviour under test.
GUARD_SECONDS = 300.0


class ControlledResult(TypedDict, total=False):
    """One candidate's result as `oneharness run --stream` publishes it, at the fields read here.

    `total=False` because this is somebody else's wire format, read defensively: the
    report carries far more than these, and a field this host does not read is not one
    it should refuse the report for. Each key below is one a journey here asserts on.
    """

    #: The argv the run spawned, whose first two words say which producer path was taken.
    command: list[str]
    #: What that producer wrote: for `codex app-server`, one JSON-RPC frame per line.
    stdout: str
    #: The model the candidate requested — its own `[harness.codex].model`, or `None`.
    model: str | None
    #: The model the server itself said the thread runs under. Absent on a release
    #: before the observation existed, which is a reading a journey makes rather than
    #: a shape it refuses.
    observed_model: str | None


def installed_producer() -> str | None:
    """The real producer binary, or `None` when this host has none installed."""
    return shutil.which(PRODUCER)


def refusing_home(directory: Path, *, base_url: str | None = None, model: str = HOME_MODEL) -> Path:
    """A codex home whose model endpoint refuses every turn, and whose credentials are nobody's.

    `base_url` is the shared refusing endpoint unless a caller brought its own recording
    one, and `model` is what this home names as the server's default — the model a
    thread runs under when the request names none.
    """
    (directory / "config.toml").write_text(
        REFUSING_PROVIDER.format(base_url=base_url or refusing_endpoint(), model=model),
        encoding="utf-8",
    )
    (directory / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "refusing-provider"}), encoding="utf-8"
    )
    return directory


def controlled_turn(
    oneharness_bin: str, codex_bin: str, home: Path, root: Path, configuration: tuple[str, ...]
) -> ControlledResult:
    """Drive one controlled codex turn for real, and return the result the run published.

    `configuration` is how the run is told which configuration to read — `--no-config`,
    or `--config <file>` for a journey whose subject is what a config's own
    `[harness.codex]` table reaches the wire as. Everything else is the path a dispatch
    takes: `--stream --control`, so the turn is driven over `codex app-server`.
    """
    ran = subprocess.run(
        [
            oneharness_bin,
            "run",
            *configuration,
            "--harness",
            PRODUCER,
            "--bin",
            f"{PRODUCER}={codex_bin}",
            "--prompt",
            LOST_PROMPT,
            "--stream",
            "--control",
            "--session",
            CONTROL_SESSION,
            "--session-dir",
            str(short_state.state_home(root) / "sessions"),
            "--cwd",
            str(root),
            "--timeout",
            "60",
        ],
        env={**_environment(home), "HOME": str(root)},
        text=True,
        capture_output=True,
        timeout=GUARD_SECONDS,
        check=False,
    )
    assert ran.returncode == 0, f"{ran.stdout}\n{ran.stderr}"
    streamed = [line for line in ran.stdout.splitlines() if '"type":"result"' in line]
    assert streamed, f"the run published no result line: {ran.stdout}\n{ran.stderr}"
    result = json.loads(streamed[-1])["report"]["results"][0]
    # The producer path is an assertion and not a detail: `codex exec` streams a
    # different vocabulary entirely, and a run that quietly took it would be reconciled
    # against frames no dispatch here ever sees.
    spawned = [Path(result["command"][0]).name, *result["command"][1:2]]
    assert spawned == [PRODUCER, "app-server"], result["command"]
    # The report is parsed JSON, and the producer's own shape is what the journeys read;
    # narrowing it to the fields they assert on is the whole of the modelling.
    return ControlledResult(
        command=list(result["command"]),
        stdout=str(result["stdout"]),
        model=result.get("model"),
        observed_model=result.get("observed_model"),
    )


def _environment(home: Path) -> dict[str, str]:
    """The environment both commands run under: this host's, with a throwaway codex home."""
    return {**os.environ, "CODEX_HOME": str(home)}
