"""A turn the real producer really lost, and the protocol schema it publishes.

`scripts/channel-serve.py` reads a harness's own machine transcript to name a monitor
turn its agent side lost. Two things reconcile that reading against reality — the drift
gate in `tests/test_lost_turn_wire_contract.py` and the journey in
`tests/e2e/test_lost_turn_wire_contract_e2e.py` — and both need the same lost turn, so
producing one lives here rather than in either.

Nothing about the producer is stood in for. A real `oneharness` runs the real `codex`
binary through `codex app-server` — the path a dispatch takes, selected by `--control`,
which is why a lost monitor turn leaves app-server frames rather than `codex exec`'s —
and the only thing arranged is that the model endpoint refuses every turn it is asked.
A refused turn is the same terminal shape as the quota refusal that cost this host its
monitor for a day, and it is the half a check can produce on demand: offline, in under a
second, with no paid account and no credential of this host's in reach.

The filter bounds a transcript whether or not a failure can be proven inside it, so the
other shape lives here too — `without_the_frames_that_prove_the_loss`, which is that same
real transcript cut back to the frames that prove nothing, which is what all 26 of this
host's oversized surfaces were.
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
from typing import Any, NamedTuple

#: The one producer whose transcript reaches that filter, as `oneharness list` ids it.
PRODUCER = "codex"

#: The field a lost turn's error records its classification in — the half of the named
#: failure that says which quota to go and look at.
CLASSIFICATION = "codexErrorInfo"

#: The prompt the lost turn carries. Never answered — the provider refuses it — but it is
#: what makes this a turn rather than a handshake.
LOST_PROMPT = "Report what has drifted from the plan."

#: The model endpoint the provider is pointed at, filled in with `refusing_endpoint`'s
#: own address: loopback, so the turn is refused at once and this reaches no network and
#: no paid account. `requires_openai_auth = false` with a placeholder key keeps the real
#: credential store out of it entirely.
REFUSING_PROVIDER = """model = "gpt-5-codex"
model_provider = "refusing"

[model_providers.refusing]
name = "refusing"
base_url = "{base_url}/v1"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
"""

#: What the endpoint refuses with. A provider that will not serve a turn answers it — 401
#: for a credential it rejects, 429 for a quota that is spent — and the status is what
#: codex classifies, so any refusal will do and this one needs no credential to be
#: plausible.
REFUSAL_STATUS = 401
REFUSAL_BODY = (
    b'{"error": {"message": "every turn is refused here", "type": "invalid_request_error"}}'
)


@lru_cache(maxsize=1)
def refusing_endpoint() -> str:
    """A local endpoint that refuses every turn, served once per process.

    A closed port, which this used, no longer loses a turn: codex reads a refused
    connection as the network being down and reconnects past both `_max_retries` keys
    until `capture`'s `--timeout` expires, reporting a deadline instead. A status is
    also the truer shape, since the refusal `scripts/channel-serve.py` names arrives as
    one rather than as a closed socket, and codex classifies it at once.

    A daemon thread on an ephemeral loopback port serves it, so nothing needs cleanup.
    """

    class Refuse(http.server.BaseHTTPRequestHandler):
        """Answer every request with the refusal, and keep the request log quiet."""

        def do_POST(self) -> None:  # noqa: N802 — the name is BaseHTTPRequestHandler's
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


#: The session name the run is addressed by. One character, because `--control` binds a
#: Unix socket under the session directory and Linux caps that address at 108 bytes.
CONTROL_SESSION = "m"

#: How long either local command may take before it is treated as hung. Both are
#: sub-second against binaries on this host, so this only ever catches a wedge — there is
#: no deadline here whose expiry is the behaviour under test.
GUARD_SECONDS = 300.0


class LostTurn(NamedTuple):
    """One turn a real producer really lost, and what it wrote about losing it."""

    #: The transcript verbatim, as the message a lost turn leaves in place of an answer.
    transcript: str
    #: Its frames, one per line, parsed.
    frames: tuple[dict[str, Any], ...]


def installed_producer() -> str | None:
    """The real producer binary, or `None` when this host has none installed."""
    return shutil.which(PRODUCER)


def refusing_home(directory: Path) -> Path:
    """A codex home whose model endpoint refuses every turn, and whose credentials are nobody's."""
    (directory / "config.toml").write_text(
        REFUSING_PROVIDER.format(base_url=refusing_endpoint()), encoding="utf-8"
    )
    (directory / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "refusing-provider"}), encoding="utf-8"
    )
    return directory


def capture(oneharness_bin: str, codex_bin: str, home: Path, root: Path) -> LostTurn:
    """Lose one turn for real, and return the transcript the producer wrote losing it.

    `--no-config` keeps this repository's own harness chains out of the selection, so the
    one candidate is the one binary named here.
    """
    ran = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--no-config",
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
            str(root / "sessions"),
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
    transcript = result["stdout"]
    return LostTurn(
        transcript=transcript,
        frames=tuple(json.loads(line) for line in transcript.splitlines() if line.strip()),
    )


def without_the_frames_that_prove_the_loss(transcript: str) -> str:
    """The same real transcript, minus the two frames that say the turn was lost.

    The other half of what reaches this channel, and by far the commoner half: all 26 of
    the oversized surfaces measured on this host on 2026-08-24 were `status: completed`
    with `error: null`, so nothing in any of them proved a failure and every one was
    republished as the monitor's own words. Capturing one directly is what this cannot
    do offline — a turn that completes needs a reachable provider and a paid account —
    so the shape is reached the other way, by removing from a real transcript the two
    frames whose absence is the whole of the difference.

    Every line that survives is the producer's own bytes, in its own order, so the
    handshake this reads an identity out of and the bookkeeping that makes up the bulk
    are exactly what the binary wrote. Line-wise rather than frame-wise for that reason:
    re-serializing would make the size the surface reports this function's rather than
    the producer's.
    """
    kept = [
        line
        for line in transcript.splitlines()
        if line.strip() and not _proves_the_loss(json.loads(line))
    ]
    assert kept, f"nothing of the transcript survived: {transcript}"
    return "\n".join(kept)


def _proves_the_loss(frame: dict[str, Any]) -> bool:
    """Whether this one frame is a frame `scripts/channel-serve.py` reads as proof.

    The filter's own two shapes, matched the way the filter matches them, and stated
    here as the producer emits them rather than imported from it: a turn whose terminal
    status is `failed`, and an error notification.
    `tests/test_lost_turn_wire_contract.py` is what holds both to the producer, so this
    restatement drifting is a failure there rather than a green here.
    """
    match frame:
        case {"method": "error"} | {"params": {"turn": {"status": "failed"}}}:
            return True
        case _:
            return False


def protocol_schema(codex_bin: str, home: Path, out: Path) -> dict[str, Any]:
    """What the producer says it emits: its own generated app-server protocol schema.

    Generated by the installed producer rather than checked in, which is the point — a
    checked-in copy would be one more restatement to drift.
    """
    generated = subprocess.run(
        [codex_bin, "app-server", "generate-json-schema", "--out", str(out)],
        env=_environment(home),
        text=True,
        capture_output=True,
        timeout=GUARD_SECONDS,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    definitions: dict[str, Any] = {}
    for bundle in sorted(out.glob("*.schemas.json")):
        definitions.update(json.loads(bundle.read_text(encoding="utf-8"))["definitions"])
    return definitions


def classification_recorded_by(frames: tuple[dict[str, Any], ...]) -> set[str]:
    """How this turn's own frames classified the refusal that ended it.

    A read of the producer's output, the way anyone opening the transcript would read it,
    so what a surface is held to naming is what the producer actually recorded.
    """
    return {
        payload["error"][CLASSIFICATION]
        for frame in frames
        if isinstance(payload := frame.get("params"), dict)
        if isinstance(payload.get("error"), dict)
        if isinstance(payload["error"].get(CLASSIFICATION), str)
    }


def _environment(home: Path) -> dict[str, str]:
    """The environment both commands run under: this host's, with a throwaway codex home."""
    return {**os.environ, "CODEX_HOME": str(home)}
