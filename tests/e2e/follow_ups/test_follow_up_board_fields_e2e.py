"""onefollowup against the `followups` board as it stands: its fields and its schema-15 tickets.

Before the cutover this host's own ticket module wrote schema-15 records and projected seven
of their values onto the board's text fields. onefollowup's record carries four of them —
`host`, `owning_runs`, `basis` and `verified_at` — so `onetaskgraph.yaml` projects only those,
and the three the board's older items filled (`Drafts`, `Transcripts`, `Witnesses`) are left
to what those items already hold. One batch, driven with the real `onefollowup` and the
installed plan store over the `followups` source as this checkout configures it, against
the loopback GitHub board `tests/github_board.py` serves carrying all seven fields:

* **a schema-15 ticket is read and updated as a bound ticket**: an earlier run's item at
  schema 15, every field filled, is found by `ticket search`, read by `ticket board-status`,
  and given this batch's evidence comment, its record and every field left as they were;
* **a copy writes the kept fields and only those**: the batch's new ticket lands with
  `Host`, `Owning runs`, `Basis` and `Verified at` written non-empty, and a re-copy of it
  after the three other fields were filled on the board leaves those values as the board held
  them.

The batch is drafted and published directly rather than through a run: its budget-inputs
and review hooks are switched off with onefollowup's own `--set`, which the verifying
journey beside this one drives with the configuration unmodified.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import follow_up_store
import pytest
import scripted_agent
from github_board import (
    BOARD,
    FOLLOWUPS_ENV_PREFIX,
    FOLLOWUPS_OPTIONS,
    FOLLOWUPS_PROJECT_NUMBER,
    HOST_FIELD_NAME,
    PROJECTED_FIELD_IDS,
    PROPOSAL,
    SIBLING_REPOSITORY,
    _BoardItemId,
    _field_writes,
    _GitHubFixture,
    _hosted,
    _Issue,
    _IssueNodeId,
    _serving_board,
)
from scratch_identity import seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

ONEFOLLOWUP = REPO_ROOT / ".venv" / "bin" / "onefollowup"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "cycle-time" / "ticket-v15.json"

#: The batch, the repository its drafts are about, and the session owning its review item.
BATCH = "board-fields-batch"
ABOUT = _hosted(SIBLING_REPOSITORY)
SESSION = "e2e-board-fields"
HOST = "verifier-board-fields"
DRAFT_BODY = (
    "## What happened\nA reader missed results.\n\n## Where\n`src/cursor.py`.\n\n"
    "## Why it is out of scope\nThe run was about something else.\n\n"
    "## Evidence\nSeen in the run's own log.\n"
)

#: The fields `onetaskgraph.yaml` projects onefollowup's record onto, and the three it leaves.
KEPT = ("Owning runs", "Basis", "Verified at")
DROPPED = ("Drafts", "Transcripts", "Witnesses")
#: What an older ticket's fields hold, as the host's own module projected them.
OLDER = {name: f"older {name.lower()}" for name in (*KEPT, *DROPPED)}

#: When the agent first verified its new ticket, as the scripted agent records it, and when
#: it verified it again before the re-copy.
FIRST_VERIFIED = "2026-11-13T12:00:00Z"
REVERIFIED = "2026-11-14T09:30:00Z"

#: The body slot the adopted store keeps an item's metadata in, as it writes one.
METADATA_SLOT = "<!-- onetaskgraph.metadata\n{}\n-->"


class Published(NamedTuple):
    """The board once the batch settled, and what the seeded item held before it."""

    seeded: _Issue
    seeded_body: str
    seeded_fields: tuple[str | None, dict[str, str]]
    filed: _Issue
    first_copy: dict[str, str]
    refilled: dict[str, str]
    recopy_writes: list[dict[str, object]]


def _schema_15_item(repository: str) -> _Issue:
    """The earlier run's schema-15 ticket, as main's host module filed it, every field set."""
    record = json.loads(FIXTURE.read_text(encoding="utf-8"))
    record.pop("budget")
    record["repository"] = repository
    record["basis"] = {repository: record["basis"].popitem()[1]}
    metadata = {"onetaskgraph.item_kind": "task", "orchestrator.follow-up": record}
    slot = METADATA_SLOT.format(json.dumps(metadata, separators=(",", ":"), sort_keys=True))
    issue = _Issue(
        item_id=_BoardItemId("PVTI_schema_15"),
        content_id=_IssueNodeId("I_schema_15"),
        number=1015,
        title="oneharness: the listing cursor skips the last page",
        body=f"## Root cause\n\nThe listing cursor stops one page early.\n\n{slot}",
        repository=SIBLING_REPOSITORY,
        origin=None,
        status=PROPOSAL.name,
        host=str(record["host"]),
        texts=dict(OLDER),
    )
    issue.board = BOARD
    BOARD.issues.append(issue)
    return issue


def _onefollowup(
    environment: dict[str, str], *arguments: str, stdin: str = ""
) -> dict[str, object]:
    """One `onefollowup` verb over this checkout's configuration, hooks switched off."""
    ran = subprocess.run(  # noqa: S603 - the pinned onefollowup
        [str(ONEFOLLOWUP), "--set", "hooks.batch_preparing=null"]
        + ["--set", "hooks.batch_settled=null", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert ran.returncode == 0, f"onefollowup {arguments} exited {ran.returncode}: {ran.stderr}"
    answer: dict[str, object] = json.loads(ran.stdout)
    return answer


def _fields(issue: _Issue) -> tuple[str | None, dict[str, str]]:
    return issue.host, dict(issue.texts)


def _published(root: Path) -> Iterator[Published]:
    registry = seeded(root / "registry", public=(ABOUT,))
    store = follow_up_store.isolated(root)
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ONEFOLLOWUP_", FOLLOWUPS_ENV_PREFIX))
    }
    environment |= store.environment | registry.environment
    environment |= {
        "ONEVCS_HOME": str(registry.home),
        follow_up_store.DESTINATION_ENV: "followups",
        "ONEFOLLOWUP_HOOKS__OWNER__SESSION": SESSION,
        "ONEFOLLOWUP_HOST_LABEL": HOST,
    }
    with _serving_board(options=FOLLOWUPS_OPTIONS, number=FOLLOWUPS_PROJECT_NUMBER) as remote:
        environment |= remote
        environment["ONETASKGRAPH_SOURCES__FOLLOWUPS__CONFIG__PACING__MIN_MUTATION_INTERVAL_MS"] = (
            "0"
        )
        # Set up as the operator set up the live board, through the plan-store verb.
        fields = subprocess.run(  # noqa: S603 - this checkout's own recipe
            ["just", "plans", "sources", "fields", "followups", "--apply"],  # noqa: S607 - `just` from the search path, as the operator runs it
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(120),
            check=False,
        )
        assert fields.returncode == 0, fields.stdout + fields.stderr
        older = _schema_15_item(ABOUT)
        seeded_body, seeded_fields = older.body, _fields(older)
        cause = str(json.loads(FIXTURE.read_text(encoding="utf-8"))["root_cause"])

        drafted = {
            title: str(
                _onefollowup(
                    environment,
                    "draft",
                    "--batch",
                    BATCH,
                    "--author",
                    "manager",
                    "--title",
                    title,
                    "--repository",
                    ABOUT,
                    stdin=DRAFT_BODY,
                )["draft"]
            )
            for title in ("The cursor skipped the last page again", "Paging drops a row")
        }
        _onefollowup(environment, "prepare", "--batch", BATCH)

        # The agent finds the earlier ticket for its root cause, reads it, and binds this
        # batch's evidence to it.
        found = _onefollowup(
            environment, "ticket", "search", "--batch", BATCH, "--ticket", cause, "cursor"
        )
        hits = found["hits"]
        assert isinstance(hits, list), found
        (item,) = [str(hit["item"]) for hit in hits if older.title == hit["title"]]
        _onefollowup(environment, "ticket", "board-status", "--batch", BATCH, str(item))
        _onefollowup(
            environment,
            "ticket",
            "comment",
            "--batch",
            BATCH,
            "--item",
            str(item),
            "--root-cause",
            cause,
            stdin=f"Host {HOST}: page 9 of 9 never rendered again at the basis.\n",
        )

        # And files the other draft as a ticket of its own.
        new_draft = drafted["Paging drops a row"]
        tickets = store.work / "tasks" / BATCH / "tickets"
        tickets.mkdir(parents=True, exist_ok=True)
        (tickets / f"{scripted_agent.NEW_ROOT_CAUSE}.md").write_text(
            scripted_agent._ticket(BATCH, ABOUT, HOST, new_draft, "Paging drops a row"),
            encoding="utf-8",
        )
        _onefollowup(environment, "ticket", "copy", "--batch", BATCH, scripted_agent.NEW_ROOT_CAUSE)
        answers = store.work / "dispositions" / f"{BATCH}.json"
        held = json.loads(answers.read_text(encoding="utf-8"))
        held["dispositions"] = [
            {
                "draft": drafted["The cursor skipped the last page again"],
                "disposition": "filed",
                "root_causes": [cause],
                "detail": "Evidence on the earlier ticket for the same root cause.",
                "kind": "root-cause",
            },
            {
                "draft": new_draft,
                "disposition": "filed",
                "root_causes": [scripted_agent.NEW_ROOT_CAUSE],
                "detail": "Verified at the basis.",
                "kind": "root-cause",
            },
        ]
        answers.write_text(json.dumps(held, indent=2) + "\n", encoding="utf-8")
        assert _onefollowup(environment, "check", "--batch", BATCH)["sound"] is True
        sent = _onefollowup(environment, "publish", "--batch", BATCH)
        outcomes = sent["outcomes"]
        assert isinstance(outcomes, list), sent
        # The earlier ticket's evidence comment, then the new ticket's create; on a refusal,
        # each receipt's error says what the store refused.
        assert [outcome["outcome"] for outcome in outcomes] == ["written", "created"], [
            json.loads(line)["outcome"].get("error")
            for line in (store.work / "receipts" / f"{BATCH}.jsonl").read_text("utf-8").splitlines()
        ]
        (filed,) = BOARD.created
        first_copy = dict(filed.texts) | {HOST_FIELD_NAME: str(filed.host)}

        # Somebody's earlier tooling fills the three fields onefollowup does not write, and the
        # batch copies its ticket again.
        # llmlint: ignore-block[tests_mirror_real_usage] The values stand for what the host's
        # deleted module projected onto a live item before the cutover, which no command this
        # checkout still runs can write; everything read and written afterwards is the real
        # onefollowup and plan store.
        refilled = {name: OLDER[name] for name in DROPPED}
        filed.texts.update(refilled)
        # llmlint: ignore-end[tests_mirror_real_usage]
        # The agent verified it again later, so the re-copy is an update the board must take.
        ticket = tickets / f"{scripted_agent.NEW_ROOT_CAUSE}.md"
        verified = ticket.read_text(encoding="utf-8")
        assert FIRST_VERIFIED in verified, verified
        ticket.write_text(verified.replace(FIRST_VERIFIED, REVERIFIED), encoding="utf-8")
        already = len(_GitHubFixture.requests)
        _onefollowup(environment, "ticket", "copy", "--batch", BATCH, scripted_agent.NEW_ROOT_CAUSE)
        assert _onefollowup(environment, "check", "--batch", BATCH)["sound"] is True
        _onefollowup(environment, "publish", "--batch", BATCH)
        recopy_writes = _field_writes(_GitHubFixture.requests[already:])
        _onefollowup(environment, "settle", "--batch", BATCH)
        yield Published(
            older, seeded_body, seeded_fields, filed, first_copy, refilled, recopy_writes
        )


@pytest.fixture(scope="module")
def published(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Published]:
    yield from _published(tmp_path_factory.mktemp("board-fields"))


def test_a_schema_15_ticket_takes_the_batchs_evidence_with_its_record_and_fields_kept(
    published: Published,
) -> None:
    older = published.seeded
    assert older.body == published.seeded_body
    assert _fields(older) == published.seeded_fields
    bodies = [str(comment["body"]) for comment in older.comments]
    marker = f'<!-- orchestrator:follow-up-comment run="{BATCH}" root_cause="'
    assert [body for body in bodies if marker in body], bodies


def test_a_copy_writes_the_kept_fields_and_leaves_the_rest_as_the_board_held_them(
    published: Published,
) -> None:
    first = published.first_copy
    assert first[HOST_FIELD_NAME] == HOST, first
    for name in KEPT:
        assert first.get(name), (name, first)
    assert BATCH in first["Owning runs"], first
    assert not {name for name in DROPPED if first.get(name)}, first

    after = dict(published.filed.texts)
    assert after["Verified at"] == REVERIFIED, after
    assert after["Owning runs"] == first["Owning runs"], after
    for name, value in published.refilled.items():
        assert after[name] == value, (name, after)
    written = {str(write.get("fieldId")) for write in published.recopy_writes}
    assert PROJECTED_FIELD_IDS["Verified at"] in written, published.recopy_writes
    dropped_ids = {PROJECTED_FIELD_IDS[name] for name in DROPPED}
    assert not written & dropped_ids, published.recopy_writes


def test_the_batch_s_records_name_this_host(published: Published) -> None:
    held = re.search(r"<!-- onetaskgraph\.metadata\n(.*?)\n-->", published.filed.body, re.DOTALL)
    assert held is not None, published.filed.body
    record = json.loads(held.group(1))["orchestrator.follow-up"]
    assert record["host"] == HOST
    assert record["owning_runs"] == [BATCH]
