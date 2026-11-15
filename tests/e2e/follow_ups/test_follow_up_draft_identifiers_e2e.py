"""The drafting command passes identifiers through as the engine and the bus name them.

`scripts/follow-up-draft.sh` — `$ORCHESTRATOR_FOLLOW_UP_DRAFT` — enforces no grammar of its
own on a run, a node or a member: the batch is the public id the engine reads for the run,
and the transcript stamp is a command line with each value shell-quoted into it. Driven
here with the real script, the real engine and the pinned onefollowup over a recorded run
whose id carries a dot, its journal holding two observer members' events: a node of that run
drafts, and its monitor drafts as a member whose name carries a single quote, a double quote
and a space; the monitor's stamp, run as the command it is, selects that member's events.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import NamedTuple

import follow_up_store
import pytest
import yaml
from probe_run_root import run_root
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

DRAFT = REPO_ROOT / "scripts" / "follow-up-draft.sh"
ENGINE_BIN = REPO_ROOT / "node_modules" / ".bin"
RUN = f"release.v2-{os.getpid()}"
NODE = "land-cursor"
MEMBER = 'o\'brien "the" monitor'
OTHER = "check-in"
ABOUT = "github.com/nickderobertis/some-service"
BODY = (
    "## What happened\nA reader missed results.\n\n## Where\n`src/cursor.py`.\n\n"
    "## Why it is out of scope\nThe run was about something else.\n\n"
    "## Evidence\nSeen in the run's own log.\n"
)
#: When each member's two events were recorded, the monitor's first.
MEMBER_EVENTS = ("2026-01-01T00:00:00.000Z", "2026-01-01T00:00:01.000Z")
OTHER_EVENTS = ("2026-01-01T00:00:02.000Z", "2026-01-01T00:00:03.000Z")


class Drafted(NamedTuple):
    """One draft's answer from onefollowup and the record it stored."""

    answer: dict[str, object]
    record: dict[str, object]


# llmlint: ignore-block[tests_mirror_real_usage] An observer member's name is the one
# `graphs/dag-scope.yaml` gives it, so no launch of this checkout produces a member whose name
# carries quotes and a space, the identifier this journey must pass through; the journal is
# written in the engine's own event shape, and everything that reads it — the drafting
# command, onefollowup and the engine's `monitor` — is real.
def _recorded_run(runs: Path) -> None:
    """A run the engine reads, its journal holding the observer's two members' events."""
    root = run_root(runs, RUN)
    events = []
    for member, stamps in ((MEMBER, MEMBER_EVENTS), (OTHER, OTHER_EVENTS)):
        for stamp, kind, payload in zip(
            stamps,
            ("member-started", "member-settled"),
            ({"runner": "library", "engine": "onejudge"}, {"completed": True, "verdict": []}),
            strict=True,
        ):
            events.append(
                {
                    "v": 1,
                    "ts": stamp,
                    "stream": "dag-scope-observer",
                    "seq": len(events),
                    "source": "agentgraph",
                    "kind": kind,
                    "labels": {
                        "run_id": "dag-scope-observer",
                        "member": member,
                        "persona": "monitor",
                        "onepipeline.run_id": RUN,
                    },
                    "payload": payload,
                    "artifacts": [],
                }
            )
    (root / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )


# llmlint: ignore-end[tests_mirror_real_usage]


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    """A manager's session over a runs root and a follow-up store of the test's own."""
    runs = tmp_path / "runs"
    _recorded_run(runs)
    store = follow_up_store.isolated(tmp_path)
    inherited = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("ONEFOLLOWUP_")
        and name not in ("ONEPIPELINE_RUN_ID", "ONEPIPELINE_NODE_ID")
    }
    return store.over(inherited) | {
        "ONEPIPELINE_RUNS_DIR": str(runs),
        "CLAUDE_CODE_SESSION_ID": "e2e-draft-identifiers",
    }


def _drafted(environment: dict[str, str], *arguments: str, title: str) -> Drafted:
    ran = subprocess.run(  # noqa: S603 - the drafting command every dispatch is handed
        [str(DRAFT), *arguments, "--title", title, "--repository", ABOUT],
        cwd=REPO_ROOT,
        env=environment,
        input=BODY,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    answer: dict[str, object] = json.loads(ran.stdout)
    text = Path(str(answer["path"])).read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---\n")[1])
    record: dict[str, object] = front["metadata"]["orchestrator.follow-up-draft"]
    return Drafted(answer, record)


def test_a_node_of_a_run_whose_public_id_has_a_dot_drafts_into_that_batch(
    environment: dict[str, str],
) -> None:
    """The batch is the engine's public id, unchanged; the stamp reads the node's turns."""
    node = environment | {"ONEPIPELINE_RUN_ID": RUN, "ONEPIPELINE_NODE_ID": NODE}

    drafted = _drafted(node, title="The cursor skips the last page")

    assert drafted.answer["batch"] == RUN, drafted.answer
    assert drafted.record["run"] == RUN, drafted.record
    assert drafted.record["author"] == {"kind": "node", "node": NODE, "member": None}
    transcript = drafted.record["transcript"]
    assert isinstance(transcript, str), drafted.record
    assert shlex.split(transcript) == ["onepipeline", "transcript", RUN, NODE], transcript


def test_the_monitor_drafts_as_a_member_whose_name_carries_quotes_and_a_space(
    environment: dict[str, str],
) -> None:
    """Recorded under the member's own name, its stamp, run, selects that member's events."""
    drafted = _drafted(
        environment, "--as", "monitor", "--member", MEMBER, "--run", RUN, title="A stale document"
    )

    assert drafted.answer["batch"] == RUN, drafted.answer
    assert drafted.record["author"] == {"kind": "monitor", "node": None, "member": MEMBER}
    transcript = drafted.record["transcript"]
    assert isinstance(transcript, str), drafted.record
    argv = shlex.split(transcript)
    assert argv[:3] == ["onepipeline", "monitor", RUN], argv
    assert json.loads(argv[4]) == {"include": [{"member": MEMBER}]}, argv

    read = subprocess.run(  # noqa: S603 - the stamp, run as the command it records
        argv,
        cwd=REPO_ROOT,
        env=environment | {"PATH": f"{ENGINE_BIN}{os.pathsep}{environment['PATH']}"},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert read.returncode == 0, read.stdout + read.stderr
    for stamp in MEMBER_EVENTS:
        assert stamp in read.stdout, read.stdout
    for stamp in OTHER_EVENTS:
        assert stamp not in read.stdout, read.stdout
