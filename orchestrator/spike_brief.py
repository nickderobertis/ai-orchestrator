"""The advisory spike-brief receipt, rendering checks and express user approval.

This is the sole reader of spike-brief.json. Receipt errors diagnose unknown advisory
state; they never alter the planning flow or the unfinished exit-status vocabulary.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NewType, TypedDict, cast
from urllib.parse import urlsplit

from orchestrator import npm_installs, run_reading

# Excluded from coverage because only a type checker runs this import: at runtime the store
# SDK it loads stays out of `unfinished`, which reads receipts under the system interpreter.
if TYPE_CHECKING:  # pragma: no cover
    from orchestrator.plan_store import QualifiedDocumentId, QualifiedProjectId

Digest = NewType("Digest", str)
ManagerSession = NewType("ManagerSession", str)

SCHEMA_VERSION = 1
RECEIPT = "spike-brief.json"
TEMPLATE_REFERENCE = "onepipeline:spike-brief"
RUN_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*\Z")
RUNS_ROOT_ENV = "ONEPIPELINE_RUNS_DIR"
DEFAULT_RUNS_ROOT = "runs"
QUALIFIED = re.compile(r"[A-Za-z0-9_-]+:[^\s:]+")
SHA256 = re.compile(r"[0-9a-f]{64}")
PLACEHOLDER = re.compile(r"(?:todo|tbd|placeholder|n/?a|none|\.\.\.|<[^>]*>)", re.IGNORECASE)
WORD_CAP = 250


class Brief(TypedDict):
    document: QualifiedDocumentId
    url: str
    digest: Digest
    surfaced_at: str


class Approval(TypedDict):
    digest: Digest
    spikes_run: run_reading.RunId
    approved_at: str


class Receipt(TypedDict):
    schema_version: Literal[1]
    plan_project: QualifiedProjectId
    board_project: QualifiedProjectId | None
    parent_run: run_reading.RunId
    spikes_run: run_reading.RunId
    writer_run: run_reading.RunId
    manager_session: ManagerSession
    brief: Brief | None
    approval: Approval | None


class Item(TypedDict):
    plan_project: QualifiedProjectId
    spikes_run: run_reading.RunId
    url: str
    state: Literal["awaiting-approval"]
    approve_command: str


def runs_root() -> Path:
    return Path(os.environ.get(RUNS_ROOT_ENV, DEFAULT_RUNS_ROOT))


def text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        raise ValueError(f"{field} must be a nonempty printable string")
    return value


def matched(value: object, field: str, pattern: re.Pattern[str]) -> str:
    held = text(value, field)
    if not pattern.fullmatch(held):
        raise ValueError(f"{field} has an invalid shape: {held!r}")
    return held


def https(value: object) -> str:
    held = text(value, "url")
    parsed = urlsplit(held)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(character.isspace() for character in held)
    ):
        raise ValueError("url must be an HTTPS board-project URL")
    # Reading port validates its numeric spelling and range before a link is relayed.
    if parsed.port == 0:
        raise ValueError("url must use a valid HTTPS port")
    return held


def timestamp(value: object, field: str) -> str:
    held = text(value, field)
    parsed = datetime.fromisoformat(held.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field} must be a UTC timestamp")
    return held


def now() -> str:
    return datetime.now(UTC).isoformat()


def validate(value: object) -> Receipt:
    if not isinstance(value, dict) or set(value) != set(Receipt.__required_keys__):
        raise ValueError("receipt must carry exactly the version 1 fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("receipt schema_version must be integer 1")
    project = matched(value["plan_project"], "plan_project", QUALIFIED)
    if not project.startswith("authoring:"):
        raise ValueError("plan_project must belong to authoring")
    board = value["board_project"]
    if board is not None:
        board_source = matched(board, "board_project", QUALIFIED).split(":", 1)[0]
        if board_source != "plans":
            from orchestrator import plan_store

            if board_source == "authoring" or board_source not in plan_store.reachable("plans"):
                raise ValueError("board_project must belong to plans or one of its routed sources")
    for field in ("parent_run", "spikes_run", "writer_run"):
        matched(value[field], field, RUN_ID)
    text(value["manager_session"], "manager_session")
    brief = value["brief"]
    if brief is not None:
        if not isinstance(brief, dict) or set(brief) != set(Brief.__required_keys__):
            raise ValueError("brief must carry document, url, digest and surfaced_at")
        document = matched(brief["document"], "document", QUALIFIED)
        if not document.startswith("authoring:"):
            raise ValueError("brief document must belong to plan_project")
        if board is None:
            raise ValueError("a surfaced brief needs a successful board copy")
        # The flow verified the copy-derived board location before writing; reads stay offline.
        https(brief["url"])
        matched(brief["digest"], "digest", SHA256)
        timestamp(brief["surfaced_at"], "surfaced_at")
    approval = value["approval"]
    if approval is not None:
        if not isinstance(approval, dict) or set(approval) != set(Approval.__required_keys__):
            raise ValueError("approval must carry digest, spikes_run and approved_at")
        matched(approval["digest"], "digest", SHA256)
        matched(approval["spikes_run"], "spikes_run", RUN_ID)
        timestamp(approval["approved_at"], "approved_at")
    return cast(Receipt, value)


def recorded_context(receipt: Receipt, board_project: object, documents: Sequence[str]) -> Receipt:
    """Validate a receipt against the local store facts its reader obtained."""
    if receipt["board_project"] is not None and board_project != receipt["board_project"]:
        raise ValueError("board_project disagrees with store correspondence")
    if receipt["brief"] and receipt["brief"]["document"] not in documents:
        raise ValueError("brief document does not belong to plan_project")
    return receipt


def read(path: Path) -> Receipt | None:
    if not path.exists():
        return None
    try:
        receipt = validate(json.loads(path.read_text()))
        if path.parent.name != receipt["parent_run"]:
            raise ValueError("receipt directory does not match parent_run")
        board_project: object = None
        documents: list[str] = []
        if receipt["board_project"] is not None:
            from orchestrator import plan_store

            recorded = plan_store.project_record(receipt["plan_project"]).get("metadata")
            copies = recorded.get(plan_store.COPIES_KEY) if isinstance(recorded, dict) else None
            board_project = (
                copies.get(receipt["board_project"].split(":", 1)[0])
                if isinstance(copies, dict)
                else None
            )
        if receipt["brief"]:
            from orchestrator import plan_store

            documents = [
                str(document.qualified_id)
                for document in plan_store.read_documents(receipt["plan_project"])
            ]
        return recorded_context(receipt, board_project, documents)
    except (ImportError, OSError, ValueError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def saved(path: Path) -> Receipt:
    """The receipt at ``path``, which its caller names as one that was written."""
    receipt = read(path)
    if receipt is None:
        raise ValueError(f"{path.parent.name}: receipt is missing")
    return receipt


@contextmanager
def locked(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write(path: Path, receipt: Receipt) -> None:
    validate(receipt)
    descriptor, stage = tempfile.mkstemp(prefix=".spike-brief-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(json.dumps(receipt, indent=2) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(stage, path)
    finally:
        if os.path.exists(stage):
            os.unlink(stage)


class StartedDelivery(TypedDict):
    schema_version: Literal[1]
    eligible: str


class Delivery(StartedDelivery, total=False):
    writer_finished: str
    copy_finished: str
    delivered: str


DELIVERY_HISTORY = "spike-brief-delivery.json"
DELIVERY_PHASES = ("eligible", "writer_finished", "copy_finished", "delivered")


def completed_delivery(history: Delivery, surfaced_at: str) -> Delivery:
    """Complete saved delivery telemetry only from the receipt's recorded surfacing."""
    return {**history, "delivered": history.get("delivered", surfaced_at)}


def read_delivery(path: Path, *, complete: bool = False) -> Delivery:
    value = json.loads(path.read_text())
    fields = Delivery.__required_keys__ | Delivery.__optional_keys__
    if not isinstance(value, dict) or not Delivery.__required_keys__ <= set(value) <= fields:
        raise ValueError("incomplete or malformed delivery telemetry")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("unknown delivery telemetry schema")
    previous: datetime | None = None
    missing = False
    for field in DELIVERY_PHASES:
        if field not in value:
            missing = True
            continue
        if missing:
            raise ValueError("delivery telemetry skips a phase")
        moment = datetime.fromisoformat(timestamp(value[field], field))
        if previous is not None and moment < previous:
            raise ValueError("delivery telemetry is out of order")
        previous = moment
    if complete and missing:
        raise ValueError("incomplete delivery telemetry")
    return cast(Delivery, value)


def write_delivery(path: Path, value: Delivery) -> None:
    from orchestrator.project_store import publish_record

    publish_record(path, json.dumps(value))


def answers(value: object, agents: Sequence[str], rendering: str) -> None:
    """Hold structured answers and their stored rendering to the template's product limits."""
    if not isinstance(value, Mapping) or set(value) != {"overview", "spikes"}:
        raise ValueError("brief answers must be overview and spikes")
    overview = substantive(value["overview"], "overview")
    listed = value["spikes"]
    if not isinstance(listed, list) or not listed:
        raise ValueError("spikes must be a nonempty list")
    named: list[str] = []
    for section in listed:
        if not isinstance(section, dict) or set(section) != {"spike", "question", "use"}:
            raise ValueError("each spike must carry spike, question and use")
        named.append(substantive(section["spike"], "spike"))
        substantive(section["question"], "question")
        substantive(section["use"], "use")
    if len(named) != len(set(named)) or set(named) != set(agents):
        raise ValueError("brief must cover exactly the actual spike-agent IDs once each")
    headings = list(re.finditer(r"^## (.+)$", rendering, re.MULTILINE))
    if [heading[1] for heading in headings] != named:
        raise ValueError("stored headings must identify each spike in answer order")
    if not headings or rendering[: headings[0].start()].strip() != overview.strip():
        raise ValueError("stored rendering must start with the overview")
    chunks = [rendering[: headings[0].start()]] + [
        rendering[
            heading.start() : headings[index + 1].start()
            if index + 1 < len(headings)
            else len(rendering)
        ]
        for index, heading in enumerate(headings)
    ]
    for name, chunk in zip(["overview", *named], chunks, strict=True):
        if len(chunk.split()) > WORD_CAP:
            raise ValueError(f"{name} exceeds its independent {WORD_CAP}-word cap")


def substantive(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or PLACEHOLDER.fullmatch(value.strip()):
        raise ValueError(f"{field} must contain a non-placeholder answer")
    return value


def digest(content: str) -> Digest:
    return Digest(hashlib.sha256(content.encode()).hexdigest())


def ended(run: str) -> bool:
    binary = npm_installs.ENGINE
    result = subprocess.run(
        [str(binary), "status", run, "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"status {run} refused: {result.stderr.strip()}")
    return terminal_reading(result.stdout, run_reading.RunId(run))


def terminal_reading(reading: str, run: run_reading.RunId) -> bool:
    """Only an authoritative ending retires the opportunity to save spike work."""
    return run_reading.decide(reading, run).decision == run_reading.Decision.ENDED


def matching_approval(receipt: Receipt) -> bool:
    brief, approval = receipt["brief"], receipt["approval"]
    return bool(
        brief
        and approval
        and approval["digest"] == brief["digest"]
        and approval["spikes_run"] == receipt["spikes_run"]
    )


def items(session: str) -> tuple[list[Item], list[str]]:
    rows: list[Item] = []
    diagnostics: list[str] = []
    for path in sorted(runs_root().glob("*/" + RECEIPT)):
        try:
            receipt = read(path)
            if receipt is None or receipt["manager_session"] != session or not receipt["brief"]:
                continue
            if matching_approval(receipt):
                from orchestrator import plan_store

                documents = plan_store.read_documents(receipt["plan_project"])
                unchanged = any(
                    doc.qualified_id == receipt["brief"]["document"]
                    and digest(doc.content) == receipt["brief"]["digest"]
                    for doc in documents
                )
                if unchanged:
                    continue
                diagnostics.append("spike brief approval invalidated by changed content")
            try:
                terminal = ended(receipt["spikes_run"])
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                diagnostics.append(f"spike brief unresolved: {exc}")
                terminal = False
            if not terminal:
                rows.append(
                    {
                        "plan_project": receipt["plan_project"],
                        "spikes_run": receipt["spikes_run"],
                        "url": receipt["brief"]["url"],
                        "state": "awaiting-approval",
                        "approve_command": f"just approve-spikes {receipt['plan_project']}",
                    }
                )
        except ValueError as exc:
            diagnostics.append(f"spike brief unresolved: {exc}")
    return rows, diagnostics


def surfaced(receipt: Receipt, session: str) -> Brief:
    """``receipt``'s surfaced brief, when ``session`` is the manager it was surfaced to."""
    brief = receipt["brief"]
    if receipt["manager_session"] != session or not brief:
        raise ValueError("approval requires this manager's surfaced brief")
    return brief


def approve(project: str, session: str) -> None:
    matched(project, "plan_project", QUALIFIED)
    paths = []
    for path in runs_root().glob("*/" + RECEIPT):
        receipt = read(path)
        if receipt and receipt["plan_project"] == project:
            paths.append(path)
    if len(paths) != 1:
        raise ValueError(f"{project}: expected one receipt, found {len(paths)}")
    path = paths[0]
    # Checked before the lock too, so an approval that cannot apply is refused at once rather
    # than after a delivery holding the lock has finished.
    surfaced(saved(path), session)
    with locked(path):
        current = saved(path)
        brief = surfaced(current, session)
        from orchestrator import plan_store

        held = {
            digest(document.content)
            for document in plan_store.read_documents(project)
            if document.qualified_id == brief["document"]
        }
        if held != {brief["digest"]}:
            raise ValueError("brief content changed; surface its current digest before approval")
        if matching_approval(current):
            return
        if ended(current["spikes_run"]):
            raise ValueError("spikes have ended; the link is retrospective")
        current["approval"] = {
            "digest": brief["digest"],
            "spikes_run": current["spikes_run"],
            "approved_at": now(),
        }
        write(path, current)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record express user approval of a surfaced brief")
    parser.add_argument("project")
    args = parser.parse_args(argv)
    try:
        approve(args.project, text(os.environ.get("ONEPIPELINE_LAUNCHER_SESSION"), "session"))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"approve-spikes: refused: {exc}", file=sys.stderr)
        return 2
    print(f"approve-spikes: approved current surfaced brief of {args.project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
