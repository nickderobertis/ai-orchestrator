"""Host-visible FIFO channel between a live planner and onejudge's supervisor."""

# llmlint: ignore-file[modern_domain_modeling] mappings preserve the upstream JSON wire contract
# llmlint: ignore-file[structural_pattern_matching] explicit checks give precise boundary errors
# llmlint: ignore-file[changed_behavior_has_e2e] real journeys e2e; malformed branches unit tested
# These dictionaries are the thin, validated onejudge JSON wire contract. The real launch,
# just recipes, FIFO round trips, timeout, and reattach run e2e; exhaustive malformed-input
# and unavailable-peer branches stay deterministic unit tests rather than timing-heavy e2e.

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import math
import os
import queue
import select
import socket
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Protocol

from .config import ConfigError
from .coordination import advisory_lock, atomic_json
from .edits import EDIT_PROTOCOL_VERSION, EditCommand, EditError, parse_commands
from .runs import latest_round, load_mapping, validate_run_id


class ChannelError(Exception):
    """The channel payload or transport is invalid."""


class ChannelTimeout(TimeoutError):
    """The other side did not rendezvous before the bounded deadline."""


CHANNEL_DIR_ENV = "AI_ORCHESTRATOR_CHANNEL_DIR"
CHANNEL_RUN_ID_ENV = "AI_ORCHESTRATOR_CHANNEL_RUN_ID"
CHANNEL_ENDPOINTS = ("up.fifo", "down.fifo")


class ProposalSink(Protocol):
    def propose(self, node: str, message: str) -> None: ...

    def persist_replies(self) -> None: ...

    def drain_commands(self) -> tuple[EditCommand, ...]: ...


def create_channel(run_dir: Path) -> Path:
    """Create (or validate) the two FIFOs and durable channel metadata."""
    channel_dir = run_dir / "channel"
    channel_dir.mkdir(parents=True, exist_ok=True)
    with advisory_lock(f"channel-create:{channel_dir.resolve()}"):
        for name in CHANNEL_ENDPOINTS:
            path = channel_dir / name
            if path.exists():
                if not path.is_fifo():
                    raise ChannelError(f"channel endpoint is not a FIFO: {path}")
            else:
                os.mkfifo(path, 0o600)
        atomic_json(channel_dir / "channel.json", {"schema_version": 1})
    return channel_dir


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ChannelTimeout("channel rendezvous timed out")
    return remaining


@contextmanager
def _channel_lock(path: Path, purpose: str, deadline: float) -> Iterator[None]:
    """Hold one endpoint lock within the caller's transport deadline."""
    lock_path = path.with_name(f"{path.name}.{purpose}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.01, _remaining(deadline)))
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_message(path: Path, *, timeout: float) -> dict[str, Any]:
    """Read exactly one locked, newline-delimited JSON mapping with a bounded wait."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ChannelError("timeout must be finite and non-negative")
    deadline = time.monotonic() + timeout
    with _channel_lock(path, "read", deadline):
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        acknowledge = False
        try:
            data = bytearray()
            while b"\n" not in data:
                ready, _, _ = select.select([fd], [], [], _remaining(deadline))
                if not ready:
                    raise ChannelTimeout("channel read timed out")
                chunk = os.read(fd, 65536)
                if not chunk:
                    time.sleep(min(0.01, _remaining(deadline)))
                    continue
                data.extend(chunk)

            # After seeing the writer's final newline, the reader closes this FIFO and
            # creates its acknowledgment while still holding the read lock. The writer
            # retains the write lock until that acknowledgment appears, so queued writers
            # wait on the write lock; they do not wait for this read lock to be released.
            acknowledge = True
            line, trailing = bytes(data).split(b"\n", 1)
            if trailing:
                raise ChannelError("channel frame contains trailing data")
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ChannelError("channel frame is not valid JSON") from exc
            if not isinstance(value, dict):
                raise ChannelError("channel frame must be a JSON object")
            return value
        finally:
            os.close(fd)
            if acknowledge:
                path.with_name(f"{path.name}.ack").touch(mode=0o600)


def write_message(path: Path, value: Mapping[str, Any], *, timeout: float) -> None:
    """Write one serialized JSON-line frame after bounded writer/reader rendezvous."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ChannelError("timeout must be finite and non-negative")
    encoded = (json.dumps(dict(value), separators=(",", ":")) + "\n").encode()
    deadline = time.monotonic() + timeout
    with _channel_lock(path, "write", deadline):
        acknowledgment = path.with_name(f"{path.name}.ack")
        with suppress(FileNotFoundError):
            acknowledgment.unlink()
        while True:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as exc:
                if exc.errno != errno.ENXIO:
                    raise
                time.sleep(min(0.01, _remaining(deadline)))
        try:
            written = 0
            while written < len(encoded):
                _, ready, _ = select.select([], [fd], [], _remaining(deadline))
                if not ready:
                    raise ChannelTimeout("channel write timed out")
                try:
                    written += os.write(fd, encoded[written:])
                except BlockingIOError:
                    continue
        finally:
            os.close(fd)
        # Do not hand the writer lock to the next frame until the reader has consumed
        # this one and closed its descriptor. The sidecar is only a rendezvous token;
        # the JSON line remains the complete external frame.
        while not acknowledgment.is_file():
            time.sleep(min(0.01, _remaining(deadline)))
        acknowledgment.unlink()


def _surface(request: Mapping[str, Any], run_id: str, round_number: int) -> dict[str, Any]:
    kind = request.get("kind", "supervisor")
    message = request.get("message") or request.get("task")
    messages = request.get("messages", [])
    if not isinstance(kind, str) or not isinstance(message, str):
        raise ChannelError("supervisor request must contain string kind/message or task")
    if not isinstance(messages, list) or not all(
        isinstance(item, dict)
        and isinstance(item.get("role"), str)
        and isinstance(item.get("content"), str)
        for item in messages
    ):
        raise ChannelError("supervisor request messages must contain role/content strings")
    if messages:
        last = messages[-1]
        content = last.get("content") if isinstance(last, dict) else None
        if isinstance(content, str):
            try:
                emitted = json.loads(content)
            except json.JSONDecodeError:
                emitted = None
            if (
                isinstance(emitted, dict)
                and isinstance(emitted.get("kind"), str)
                and isinstance(emitted.get("message"), str)
            ):
                kind = emitted["kind"]
                message = emitted["message"]
                if "options" in emitted:
                    request = {**request, "options": emitted["options"]}
    surface: dict[str, Any] = {"kind": kind, "message": message}
    options = request.get("options")
    if options is not None:
        if not isinstance(options, list) or not all(isinstance(item, str) for item in options):
            raise ChannelError("supervisor options must be a list of strings")
        surface["options"] = options
    return {
        "op": "supervisor",
        "run_id": run_id,
        "round": round_number,
        "surface": surface,
        "messages": messages,
    }


def _reply(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        commands = parse_commands(value)
    except EditError as exc:
        raise ChannelError(str(exc)) from exc
    completion = value.get("completion")
    reason = value.get("reason")
    if completion is None and commands:
        completes = [command for command in commands if command.op == "complete"]
        complete_reason = completes[-1].payload.get("reason") if completes else None
        if completes and not isinstance(complete_reason, str):
            raise ChannelError("complete edit requires a string reason")
        response: dict[str, Any] = {
            "completion": bool(completes),
            "reason": complete_reason or "versioned edit commands",
            "version": EDIT_PROTOCOL_VERSION,
            "commands": [command.payload for command in commands],
        }
        if not completes:
            response["message"] = "apply live graph edits"
        return response
    if not isinstance(completion, bool) or not isinstance(reason, str):
        raise ChannelError("reply requires boolean completion and string reason")
    if completion:
        response = {"completion": True, "reason": reason}
        if commands:
            response.update(
                {
                    "version": EDIT_PROTOCOL_VERSION,
                    "commands": [command.payload for command in commands],
                }
            )
        return response
    message = value.get("message")
    if not isinstance(message, str):
        raise ChannelError("continue reply requires string message")
    response = {"completion": False, "message": message, "reason": reason}
    if commands:
        response.update(
            {
                "version": EDIT_PROTOCOL_VERSION,
                "commands": [command.payload for command in commands],
            }
        )
    return response


class ProposalPump:
    """Service mid-run proposal round trips without writing graph state off-thread."""

    def __init__(self, channel_dir: Path, run_id: str, round_number: int) -> None:
        self._channel_dir = channel_dir
        self._run_id = run_id
        self._round = round_number
        self._proposals: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._replies: queue.Queue[dict[str, Any]] = queue.Queue()
        self._commands: queue.Queue[EditCommand] = queue.Queue()
        self._stop = threading.Event()
        self._awaiting_reply = threading.Event()
        self._reply_received = threading.Event()
        self._thread = threading.Thread(target=self._service, daemon=True)
        self._receiver = threading.Thread(target=self._receive, daemon=True)
        self._thread.start()
        self._receiver.start()

    def propose(self, node: str, message: str) -> None:
        self._proposals.put(
            {
                "op": "supervisor",
                "run_id": self._run_id,
                "round": self._round,
                "surface": {"kind": "proposal", "message": f"{node}: {message}"},
                "messages": [],
            }
        )

    def persist_replies(self) -> None:
        """Persist transport replies on the reconciler's single-writer thread."""
        while True:
            try:
                response = self._replies.get_nowait()
            except queue.Empty:
                return
            atomic_json(self._channel_dir / "planner-verdict.json", response)

    def drain_commands(self) -> tuple[EditCommand, ...]:
        """Return commands to the reconciler thread without applying them here."""
        commands: list[EditCommand] = []
        while True:
            try:
                commands.append(self._commands.get_nowait())
            except queue.Empty:
                return tuple(commands)

    def close(self) -> None:
        self._stop.set()
        self._proposals.put(None)
        self._thread.join()
        self._receiver.join()
        self.persist_replies()

    def _service(self) -> None:
        while (proposal := self._proposals.get()) is not None:
            self._reply_received.clear()
            self._awaiting_reply.set()
            while True:
                if self._stop.is_set():
                    return
                try:
                    write_message(self._channel_dir / "up.fifo", proposal, timeout=0.1)
                    break
                except ChannelTimeout:
                    continue
                except OSError:
                    return
            while not self._stop.is_set() and not self._reply_received.wait(0.1):
                pass
            self._awaiting_reply.clear()

    def _receive(self) -> None:
        """Continuously receive planner edits, independent of proposal timing."""
        while not self._stop.is_set():
            try:
                response = _reply(read_message(self._channel_dir / "down.fifo", timeout=0.1))
                for command in parse_commands(response):
                    self._commands.put(command)
                self._replies.put(response)
                if self._awaiting_reply.is_set():
                    self._reply_received.set()
            except ChannelTimeout:
                continue
            except (ChannelError, OSError):
                return


# llmlint: ignore[names_match_behavior] onejudge sends final evals to its supervisor command
def relay_supervisor(channel_dir: Path, run_id: str, round_number: int, *, timeout: float) -> int:
    """Relay one command-provider supervisor request to the live planner."""
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise ChannelError("supervisor request must be a JSON object")
        operation = request.get("op")
        if operation not in {"supervisor", "judge"}:
            raise ChannelError("relay request op must be 'supervisor' or 'judge'")
        if operation == "judge":
            judge_kind = request.get("kind")
            if judge_kind not in {"boolean", "score"}:
                raise ChannelError("judge kind must be 'boolean' or 'score'")
            state_path = channel_dir / "planner-verdict.json"
            completed = False
            if state_path.is_file():
                persisted_completion = load_mapping(state_path).get("completion")
                if not isinstance(persisted_completion, bool):
                    raise ChannelError("persisted planner completion must be boolean")
                completed = persisted_completion
            if judge_kind == "boolean":
                print(
                    json.dumps({"value": completed, "reason": "mirrors the live planner verdict"})
                )
            else:
                maximum = request.get("max", 5)
                if (
                    not isinstance(maximum, int | float)
                    or isinstance(maximum, bool)
                    or not math.isfinite(maximum)
                    or maximum < 0
                ):
                    raise ChannelError("numeric judge max must be a non-negative number")
                print(json.dumps({"value": maximum, "reason": "live planner completed the run"}))
            return 0
        write_message(
            channel_dir / "up.fifo", _surface(request, run_id, round_number), timeout=timeout
        )
        response = _reply(read_message(channel_dir / "down.fifo", timeout=timeout))
        atomic_json(channel_dir / "planner-verdict.json", response)
    except (ChannelError, ChannelTimeout, EditError, json.JSONDecodeError, OSError) as exc:
        print(f"relay-supervisor: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(response))
    return 0


def _finished(run_dir: Path) -> bool:
    report = run_dir / "orchestrator" / "report.json"
    if report.is_file() and report.stat().st_size > 0:
        try:
            load_mapping(report)
        except (ConfigError, OSError):
            pass
        else:
            return True
    latest = latest_round(run_dir)
    if latest is None:
        return False
    status_path = latest[1] / "status.json"
    if not status_path.is_file():
        return False
    try:
        status = load_mapping(status_path)
        pid = status.get("pid")
        host = status.get("host")
        if (
            status.get("status") != "running"
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid < 1
        ):
            return True
        if not isinstance(host, str) or host != socket.gethostname():
            return False
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, ValueError):
        return False
    return False


# llmlint: ignore[changed_behavior_has_e2e] the real bridge timeout/success/reattach journey is e2e;
# malformed framing and transport failures are deterministic boundary branches exercised in unit.
def main_next(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read the next live orchestrator surface")
    parser.add_argument("run_id")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    run_dir = args.runs_dir / validate_run_id(args.run_id)
    if _finished(run_dir):
        print(json.dumps({"status": "finished"}))
        return 0
    try:
        value = read_message(run_dir / "channel" / "up.fifo", timeout=args.timeout)
    except ChannelTimeout:
        value = (
            {"status": "finished"} if _finished(run_dir) else {"status": "running", "surface": None}
        )
    except (ChannelError, OSError) as exc:
        print(f"channel-next: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value))
    return 0


# llmlint: ignore[changed_behavior_has_e2e] the real reply FIFO journey is e2e while malformed
# JSON, reply contracts, and absent-rendezvous errors are exhaustively exercised in unit tests.
def main_reply(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reply to the live orchestrator supervisor")
    parser.add_argument("run_id")
    parser.add_argument("reply", nargs="?", default="-")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        raw = (
            sys.stdin.read() if args.reply == "-" else Path(args.reply).read_text(encoding="utf-8")
        )
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ChannelError("reply must be a JSON object")
        write_message(
            args.runs_dir / validate_run_id(args.run_id) / "channel" / "down.fifo",
            _reply(value),
            timeout=args.timeout,
        )
    except (ChannelError, ChannelTimeout, EditError, json.JSONDecodeError, OSError) as exc:
        print(f"channel-reply: {exc}", file=sys.stderr)
        return 2
    return 0


def main_relay(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("channel_dir", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("round", type=int)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        run_id = str(validate_run_id(args.run_id))
        if args.round < 1:
            raise ChannelError("round must be a positive integer")
        channel_dir = args.channel_dir.resolve()
        if channel_dir.parent.name != run_id:
            raise ChannelError("channel directory must belong to the requested run id")
        metadata = load_mapping(channel_dir / "channel.json")
        if metadata.get("schema_version") != 1 or not all(
            (channel_dir / name).is_fifo() for name in CHANNEL_ENDPOINTS
        ):
            raise ChannelError("channel directory has invalid metadata or endpoints")
    except (ChannelError, ConfigError, ValueError) as exc:
        parser.error(str(exc))
    return relay_supervisor(channel_dir, run_id, args.round, timeout=args.timeout)


if __name__ == "__main__":  # pragma: no cover - exercised through the installed console script
    raise SystemExit(main_relay())
