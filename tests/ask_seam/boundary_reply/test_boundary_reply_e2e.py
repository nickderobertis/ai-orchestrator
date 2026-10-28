"""`just channel-reply` keeps a private term out of what reaches a mixed run's public node.

The user wants private work to inform public libraries while nothing public reveals a private
name, path, identifier or content, and a manager's note or edit to a public node is public
output: the node's worker reads it, and what that worker writes is published. So a mixed
run's replies are judged against the run's boundary before anything is appended: a `note`
and an `amend` carrying a private term, addressed to the public node, are refused whole —
the recipe exits non-zero, nothing reaches the run's channel or its journal, and the refusal
says where the term was without quoting it — and the same note and edit without the term are
taken and journalled.

The run is launched by the real `just orchestrate` and every envelope is sent by the real
`just channel-reply`, so whatever judges an envelope receives the run context the engine
itself supplies; nothing here calls a validator directly. The plan is mixed by the scratch
registry's rules — one synthetic public repository and one synthetic private one — and is
the shape the engine accepts: a private `publish: "preserve"` producer and a public consumer
joined by an export edge, behind a human gate nobody attests, so nothing is dispatched and the
reply is the run's single writer. Only the paid model is stood in, at the provider boundary,
exactly as `tests/ask_seam/channel_reply/test_channel_reply_e2e.py` stands it in.

The plan is stored as the boundary guidance prescribes it: one private coordinating project
in a source declared private, under the tracked configuration's write screening, holding a
private producer and a public consumer that each keep their own visibility — no mode, setting
or metadata key makes the consumer public. The journey runs in a sparse checkout of its own
declared inputs so that its configuration is its own, and holds that configuration to being
screened, with the fixture source declared private, before it authors anything.
`tests/e2e/screened_export/` executes the same shape to completion; this journey holds the
run at its gate, because the reply seam is what it proves.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This *is* the edge: the
journey spends a real launch and is behind its own Nx project, keyed on `askSeamBoundaryReply`
and selected by directory, as `tests/ask_seam/AGENTS.md` states for every journey here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, cast

import pytest
import short_state
import yaml
from nx_workspace import share_this_checkouts_install
from planner_channel import BUS_CONFIG
from project_fixtures import helper, register_stand_in
from waits import deadline
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT as TRACKED_ROOT

REPO_ROOT = TRACKED_ROOT

FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: What the doubled provider answers a review turn the criteria validator spends.
PASSING_VERDICT = {"passes": True, "findings": []}

LAUNCHING_SESSION = "e2e-boundary-reply"
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_REPLY_TIMEOUT_SECONDS",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)

#: Synthetic, neutral identities; the scratch registry's rules make them what they are.
PUBLIC_REPOSITORY = "github.com/example-public-org/open-widget"
PRIVATE_REPOSITORY = "github.com/example-private-org/quiet-harbor"

#: The private term the refused envelopes carry: the private repository's `owner/name`,
#: which onevcs matches wherever it stands.
PRIVATE_TERM = "example-private-org/quiet-harbor"

RULES = """version: 4
trailer_prefix: Orchestrator-
rules:
  - match: {host: github.com, owner: example-public-org}
    publication: local-direct
    approvals: none
    visibility: public
  - match: {host: github.com, owner: example-private-org}
    publication: local-direct
    approvals: none
    visibility: private
default:
  publication: local-direct
  approvals: none
"""

GATE = "gate"
PRODUCER = "write-the-example"
CONSUMER = "page-by-cursor"

#: What `just channel-reply` exits with for every refusal.
REPLY_REFUSED = 2


#: The run ids this suite's worker owns.
SUITE = hashlib.sha256(f"{TRACKED_ROOT}\0{os.getpid()}".encode()).hexdigest()[:8]

#: The note and the amendment, each sent once carrying the private term and once without.
NOTE = "Follow the plain cursor layout{term} when you page the listing."
AMENDMENT = (
    "The listing answers an opaque cursor it issued{term} and refuses one it did not, and a "
    "request-level test drives both paths end to end."
)


def _with(text: str, term: bool) -> str:
    return text.format(term=f", as {PRIVATE_TERM} does," if term else "")


class Mixed(NamedTuple):
    environment: dict[str, str]
    run: str
    channel: Path
    root: Path


def _task(what: str) -> str:
    return f"## What\n{what}\n\n## Why\nHold the run.\n\n## Acceptance criteria\n- Done."


def _just(
    *arguments: str, environment: dict[str, str], stdin: str | None = None, seconds: float = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _environment(tmp_path: Path) -> dict[str, str]:
    """A scratch registry holding the two synthetic identities, and the stand-ins."""
    home = tmp_path / "onevcs"
    home.mkdir()
    environment = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        environment.pop(name, None)
    environment.pop("VIRTUAL_ENV", None)
    environment["ONEVCS_HOME"] = str(home)
    rules = tmp_path / "rules.yml"
    rules.write_text(RULES, encoding="utf-8")
    applied = subprocess.run(
        ["onevcs", "rules", "apply", "--base", str(rules)],
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert applied.returncode == 0, applied.stderr
    for origin in (PUBLIC_REPOSITORY, PRIVATE_REPOSITORY):
        register_stand_in(home, origin, tmp_path / "stand-ins" / origin.replace("/", "-"))
    environment["CLAUDE_CODE_SESSION_ID"] = LAUNCHING_SESSION
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    # llmlint: ignore[e2e_not_mocked] Only the paid provider's answer is scripted.
    environment["FAKE_CODEX_ANSWERS"] = json.dumps([json.dumps(PASSING_VERDICT)])
    environment["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    return environment


@pytest.fixture(scope="module")
def mixed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Mixed]:
    """A mixed run launched through the real `just orchestrate`, held at a human gate."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    tmp_path = tmp_path_factory.mktemp("boundary-reply")
    global REPO_ROOT  # noqa: PLW0603 - this module's recipe checkout, owned by its fixture
    REPO_ROOT = tmp_path / "checkout"
    REPO_ROOT.mkdir()
    # Copy only this journey's declared runtime inputs, including the package modules
    # its support units carry. This is a sparse checkout, not a whole-tree copy.
    pending = [TRACKED_ROOT / "tests/ask_seam/boundary_reply/project.json"]
    visited: set[Path] = set()
    while pending:
        declaration = pending.pop()
        if declaration in visited:
            continue
        visited.add(declaration)
        project = json.loads(declaration.read_text(encoding="utf-8"))
        for dependency in project.get("implicitDependencies", []):
            pending.append(
                TRACKED_ROOT
                / "tests/support"
                / dependency.removeprefix("support-").replace("-", "_")
                / "project.json"
            )
        for inputs in project["namedInputs"].values():
            for named in inputs:
                relative = named.replace("{workspaceRoot}/", "").replace(
                    "{projectRoot}/", str(declaration.parent.relative_to(TRACKED_ROOT)) + "/"
                )
                if relative.startswith("tests/"):
                    continue
                original = TRACKED_ROOT / relative
                if original.is_file():
                    target = REPO_ROOT / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
    install = share_this_checkouts_install(REPO_ROOT)
    # The journey's configuration is the tracked one, screened, and the source its project is
    # authored in is declared private; a copy that lost either would prove nothing about the
    # screened store this journey stands on.
    configuration = yaml.safe_load((REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8"))
    assert configuration["write_policy"]["check_command"][:3] == ["onevcs", "boundary", "check"]
    assert configuration["sources"]["test-fixtures"]["visibility"] == "private"
    environment = {**_environment(tmp_path), **install}
    run = f"boundary-reply-{SUITE}"
    plan = tmp_path / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "page the listing by an opaque cursor"},
                "name": run,
                "tasks": [
                    {"id": GATE, "kind": "human", "task": _task("Approve.")},
                    {
                        "id": PRODUCER,
                        "persona": "engineer",
                        "repo": PRIVATE_REPOSITORY,
                        "title": "feat: write a generic cursor example",
                        "publish": "preserve",
                        "deps": [GATE],
                        "task": _task("Write a generic cursor example."),
                    },
                    {
                        "id": CONSUMER,
                        "persona": "engineer",
                        "repo": PUBLIC_REPOSITORY,
                        "title": "feat: page the listing by an opaque cursor",
                        "deps": [PRODUCER],
                        "task": _task("Page the listing by an opaque cursor."),
                    },
                ],
                "export_edges": [
                    {
                        "from": PRODUCER,
                        "to": CONSUMER,
                        "source_directory": "examples/cursor",
                        "target_directory": "examples/cursor",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    # Authored through this checkout's own screened store, into its private fixture source.
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), str(TRACKED_ROOT / "tests"), str(TRACKED_ROOT / "tests/e2e")]
    )
    authored = subprocess.run(
        [
            sys.executable,
            "-c",
            "from project_fixtures import project_from_plan; import pathlib, sys; "
            "print(project_from_plan(pathlib.Path(sys.argv[1])))",
            str(plan),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert authored.returncode == 0, authored.stdout + authored.stderr
    qualified = authored.stdout.strip()
    launch = _just(
        "orchestrate",
        qualified,
        "--dag-graph",
        "off",
        "--success-hook=",
        "--failure-hook=",
        environment=environment,
        seconds=600,
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"
    runs = Path(environment["ONEPIPELINE_RUNS_DIR"])
    held = Mixed(environment, run, runs / run / "channel", runs / run)
    try:
        _undriven(held)
        yield held
    finally:
        _just("stop", run, environment=environment, seconds=60)


def _undriven(mixed: Mixed) -> None:
    """Wait until nothing drives the run, so the reply is its single writer."""
    limit = deadline(120)
    while True:
        status = _just("status", mixed.run, "--json", environment=mixed.environment)
        assert status.returncode == 0, status.stderr
        if json.loads(status.stdout)["driven"] is False:
            return
        assert time.monotonic() < limit, f"run {mixed.run} is still driven:\n{status.stdout}"
        time.sleep(0.5)


def _records(mixed: Mixed, queue: str) -> int:
    """How many records the run's `queue` holds, as the bus's own `status` counts them."""
    read = subprocess.run(
        ["onemessagebus", "status", queue, "--config", str(BUS_CONFIG)]
        + ["--transport-dir", str(mixed.channel)],
        cwd=REPO_ROOT,
        env=mixed.environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert read.returncode == 0, read.stderr
    (status,) = cast(list[dict[str, Any]], json.loads(read.stdout))
    return int(status["records"])


def _journal(mixed: Mixed) -> str:
    """The run's own journal, whole, which is where an accepted edit is recorded."""
    written = mixed.root / "events.jsonl"
    return written.read_text(encoding="utf-8") if written.exists() else ""


def _committed(mixed: Mixed, mentioning: str) -> list[dict[str, Any]]:
    return [
        event
        for event in (json.loads(line) for line in _journal(mixed).splitlines() if line.strip())
        if event.get("kind") == "edit-committed" and mentioning in json.dumps(event)
    ]


def _send(mixed: Mixed, envelope: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """Send one envelope as a manager does: piped into `just channel-reply`."""
    return _just(
        "channel-reply",
        mixed.run,
        environment=mixed.environment,
        stdin=json.dumps(envelope),
        seconds=300,
    )


class Reply(NamedTuple):
    """One envelope a journey sends, and the text whose term decides its fate."""

    envelope: dict[str, Any]
    text: str
    #: What the run's journal records once the envelope is taken: a command's own text, or
    #: for a reply's message — which the channel's `replies` queue carries, not the journal
    #: — the neutral note beside it.
    journalled: str
    #: Whether the envelope carries a message the channel's `replies` queue takes.
    message: bool = False


def _note(term: bool) -> Reply:
    text = _with(NOTE, term)
    command = {"op": "note", "id": CONSUMER, "addressee": "worker", "text": text}
    return Reply({"version": 3, "commands": [command]}, text, text)


def _amend(term: bool) -> Reply:
    text = _with(AMENDMENT, term)
    command = {"op": "amend", "id": CONSUMER, "text": text}
    return Reply({"version": 3, "commands": [command]}, text, text)


#: A reply's own message, which addresses no node and so is judged as public, riding beside
#: a note that is neutral either way.
MESSAGE = "Carry on with the cursor{term}; the gate is mine to attest."


def _reply(term: bool) -> Reply:
    text = _with(MESSAGE, term)
    neutral = _note(False)
    envelope = {"version": 3, "message": text, "commands": neutral.envelope["commands"]}
    return Reply(envelope, text, neutral.text, message=True)


#: Every kind of reply a manager sends a mixed run, each carrying the term in one place.
BUILDS = pytest.mark.parametrize("build", [_note, _amend, _reply], ids=["note", "amend", "reply"])


@BUILDS
def test_a_private_term_addressed_to_the_public_node_is_refused_whole(
    mixed: Mixed, build: Callable[[bool], Reply]
) -> None:
    """The recipe exits non-zero, nothing is appended or journalled, and the term is not quoted."""
    reply = build(True)
    queues = {queue: _records(mixed, queue) for queue in ("replies", "commands")}
    journal = _journal(mixed)

    sent = _send(mixed, reply.envelope)

    assert sent.returncode == REPLY_REFUSED, f"{sent.returncode}\n{sent.stdout}{sent.stderr}"
    assert sent.stdout == "", f"a refused reply printed a receipt: {sent.stdout}"
    assert sent.stderr.strip(), "the refusal gave no reason"
    assert PRIVATE_TERM not in sent.stderr, "the refusal quotes the private term"
    assert "quiet-harbor" not in sent.stderr
    assert {queue: _records(mixed, queue) for queue in queues} == queues
    assert _journal(mixed) == journal, "the refused envelope reached the run's journal"
    assert not _committed(mixed, reply.text)


@BUILDS
def test_the_same_reply_without_the_term_is_taken_and_journalled(
    mixed: Mixed, build: Callable[[bool], Reply]
) -> None:
    """The neutral twin of each refused reply is applied, and the journal records it."""
    reply = build(False)
    replies = _records(mixed, "replies")

    sent = _send(mixed, reply.envelope)

    assert sent.returncode == 0, f"the clean reply was refused:\n{sent.stdout}{sent.stderr}"
    receipt = json.loads(sent.stdout.splitlines()[-1])
    assert receipt["state"] == "applied", receipt
    assert reply.journalled in _journal(mixed), "the accepted reply never reached the journal"
    if reply.message:
        assert _records(mixed, "replies") == replies + 1, "the message never reached the channel"
