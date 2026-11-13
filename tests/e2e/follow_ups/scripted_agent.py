#!/usr/bin/env python3
"""The follow-up agent's choices, for a journey whose paid model is doubled.

`tests/e2e/fake_codex.py` stands in for the provider and runs, on a turn whose prompt carries
a scripted marker, the argument vectors a journey keys to that marker. This is the one those
journeys key: it decides what a model would — which draft becomes a ticket, which goes as
evidence onto a ticket already on the board, what the budget input is disposed as, what each
person's comment is answered with — and does it with the real programs a model would run:
`onefollowup ticket validate|copy|comment` and `onefollowup check`, the plan store's own
`task comment add`, and the files onefollowup's task says the agent writes. Every record any
of it leaves is those programs'.

    scripted_agent.py verify <checkout> <batch> <new-draft-title> <evidence-draft-title> \
        <existing-item> <existing-root-cause>
    scripted_agent.py feedback <checkout> <batch>

It reads onefollowup's store from `ONEFOLLOWUP_STORE`, which the journey's launch exported
and the dispatch inherited.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

#: The ticket the verifying agent files for the first draft.
NEW_ROOT_CAUSE = "listing-cursor-skips-last-page"

#: A basis commit, as the agent would record from the repository's base branch.
BASIS = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _run(checkout: Path, argv: list[str], stdin: str = "") -> dict[str, object]:
    ran = subprocess.run(  # noqa: S603 - the real onefollowup, as the task tells the agent
        argv, cwd=checkout, input=stdin, text=True, capture_output=True, check=False
    )
    if ran.returncode not in (0, 1) or not ran.stdout.strip():
        raise SystemExit(f"{argv} exited {ran.returncode}: {ran.stdout}{ran.stderr}")
    answer: dict[str, object] = json.loads(ran.stdout)
    return answer


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _ticket(batch: str, repository: str, host: str, draft: str, title: str) -> str:
    record = {
        "schema": 14,
        "root_cause": NEW_ROOT_CAUSE,
        "repository": repository,
        "created_by_run": batch,
        "owning_runs": [batch],
        "drafts": [draft],
        "basis": {repository: BASIS},
        "verified_at": "2026-11-13T12:00:00Z",
        "host": host,
        "frequency": "intermittent",
    }
    sections = {
        "Root cause": "The listing cursor stops one page early, so a reader misses the last page.",
        "Impact": (
            "**Who is affected.** Readers of long listings.\n"
            "**What it costs them.** The last page of results.\n"
            "**What it costs the product owner.** Some readers miss results each week.\n"
            "**Cost of the workaround.**\n"
            "- Applying it: one more request per listing, by the reader\n"
            "- Side effects on the outcome: none\n"
            "- Discovery: the failure names it\n\n"
            "- Severity: high\n"
            "- Workaround: request the last page by number\n"
            "- Severity with the workaround: medium"
        ),
        "Examples": f"{title}.",
        "Suggested fix": (
            "Stop the cursor after the last page rather than before it.\n\n"
            "**Contributing locations.**\n- some-service `src/cursor.py`: stops one page early"
        ),
        "Owning batches": f"- {batch}",
        "Evidence": f"Host {host}: page 9 of 9 never rendered at the basis.",
    }
    body = "".join(f"## {name}\n\n{text}\n\n" for name, text in sections.items())
    name = repository.rsplit("/", 1)[-1]
    return (
        f'---\ntitle: "{name}: {title}"\nmetadata:\n  orchestrator.follow-up: '
        f"{json.dumps(record)}\n---\n{body.rstrip()}\n"
    )


def verify(checkout: Path, batch: str, new: str, evidence: str, item: str, cause: str) -> None:
    """File the first draft as a ticket, the second as evidence on ``item``, dispose the rest."""
    store = Path(os.environ["ONEFOLLOWUP_STORE"])
    work = store / ".onefollowup"
    answers = work / "dispositions" / f"{batch}.json"
    held = json.loads(answers.read_text(encoding="utf-8"))
    shown = _run(checkout, ["onefollowup", "config", "show"])
    host = str(shown["config"]["host_label"])  # type: ignore[index]
    drafts = {draft for draft in held["drafts"]}
    new_draft = next(draft for draft in drafts if draft.endswith(_slug(new)))
    evidence_draft = next(draft for draft in drafts if draft.endswith(_slug(evidence)))
    repository = "github.com/nickderobertis/some-service"

    tickets = work / "tasks" / batch / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    (tickets / f"{NEW_ROOT_CAUSE}.md").write_text(
        _ticket(batch, repository, host, new_draft, new), encoding="utf-8"
    )
    _run(checkout, ["onefollowup", "ticket", "validate", "--batch", batch, NEW_ROOT_CAUSE])
    _run(checkout, ["onefollowup", "ticket", "copy", "--batch", batch, NEW_ROOT_CAUSE])
    _run(
        checkout,
        ["onefollowup", "ticket", "comment", "--batch", batch, "--item", item]
        + ["--root-cause", cause],
        f"Host {host}: the export dialog dropped a unicode title again at the basis.\n",
    )

    dispositions = [
        {
            "draft": new_draft,
            "disposition": "filed",
            "root_causes": [NEW_ROOT_CAUSE],
            "detail": "Verified at the basis.",
            "kind": "root-cause",
        },
        {
            "draft": evidence_draft,
            "disposition": "filed",
            "root_causes": [cause],
            "detail": "Evidence on the existing item for the same root cause.",
            "kind": "root-cause",
        },
    ]
    # Every other input the batch was prepared with, the budget overrun among them.
    for entry in sorted(drafts - {new_draft, evidence_draft}):
        dispositions.append(
            {
                "draft": entry,
                "disposition": "not-reproducible",
                "root_causes": [],
                "detail": "The change landed within its budget when re-measured at the basis.",
                "kind": "root-cause",
            }
        )
    held["dispositions"] = dispositions
    answers.write_text(json.dumps(held, indent=2) + "\n", encoding="utf-8")
    _run(checkout, ["onefollowup", "check", "--batch", batch])


def _ticked(line: str, label: str) -> str | None:
    prefix = f"- {label}: `"
    return line[len(prefix) : -1] if line.startswith(prefix) and line.endswith("`") else None


def feedback(checkout: Path, batch: str) -> None:
    """Answer every comment each unanswered feedback file of ``batch`` quotes, once."""
    store = Path(os.environ["ONEFOLLOWUP_STORE"])
    for quoted_file in sorted((store / ".onefollowup" / "feedback" / batch).glob("*.md")):
        account = quoted_file.with_name(f"{quoted_file.stem}.responses.json")
        if account.exists():
            continue
        responses = []
        issue = comment = opening = marker = None
        for line in quoted_file.read_text(encoding="utf-8").splitlines():
            found = re.match(
                r'<!-- orchestrator:follow-up-quoted issue="([^"]+)" comment="([^"]+)"', line
            )
            if found:
                issue, comment = found.group(1), found.group(2)
                opening = marker = None
                continue
            opening = _ticked(line, "Reply opening") or opening
            marker = _ticked(line, "Marker if it confirms") or marker
            if issue and comment and opening and marker:
                posted = subprocess.run(  # noqa: S603 - the store's own comment verb
                    ["onetaskgraph", "task", "comment", "add", issue, "--author", "follow-up"]
                    + ["--json"],
                    cwd=store,
                    input=f"{opening}\n\nThank you: the ticket stands as written.\n\n{marker}\n",
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if posted.returncode != 0:
                    raise SystemExit(f"the reply was refused: {posted.stdout}{posted.stderr}")
                reply = _reply_id(json.loads(posted.stdout))
                responses.append(
                    {
                        "comment": comment,
                        "issue": issue,
                        "action": "Read the ticket against the comment.",
                        "reply": reply,
                        "reply_url": None,
                        "reply_fallback": None,
                        "verdict": "confirms",
                    }
                )
                issue = comment = opening = marker = None
        account.write_text(
            json.dumps(
                {
                    "schema": 3,
                    "run": batch,
                    "feedback": quoted_file.name,
                    "responses": responses,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _run(checkout, ["onefollowup", "comments", "check", "--feedback", str(quoted_file)])


def _reply_id(answer: object) -> str:
    """The id the store gave the comment it added."""
    if isinstance(answer, dict) and isinstance(answer.get("id"), str):
        return str(answer["id"])
    raise SystemExit(f"the store named no comment id: {answer}")


def main(argv: list[str]) -> None:
    mode, checkout = argv[0], Path(argv[1])
    if mode == "verify":
        verify(checkout, *argv[2:7])
    else:
        feedback(checkout, argv[2])


if __name__ == "__main__":
    main(sys.argv[1:])
