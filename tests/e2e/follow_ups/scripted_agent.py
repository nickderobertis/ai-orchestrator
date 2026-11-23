#!/usr/bin/env python3
"""The follow-up agent's choices, for a journey whose paid model is doubled.

`tests/e2e/fake_codex.py` stands in for the provider and runs, on a turn whose prompt carries
a scripted marker, the argument vectors a journey keys to that marker. This is the one those
journeys key: it decides what a model would — which draft becomes a ticket, which goes as
evidence onto a ticket already on the board, what the budget input is disposed as, what each
person's comment is answered with, what each groomed ticket's verdict is — and does it with
the real programs a model would run: `onefollowup ticket validate|copy|comment`, `onefollowup
check` and `onefollowup groom check`, the plan store's own `task comment add`, and the files
onefollowup's task says the agent writes. Every record any of it leaves is those programs'.

    scripted_agent.py verify <checkout> <batch> <new-draft-title> <evidence-draft-title> \
        <existing-item> <existing-root-cause>
    scripted_agent.py feedback <checkout> <batch>
    scripted_agent.py file <checkout> <batch> <root-cause>
    scripted_agent.py groom <checkout> <verdicts-by-title.json>

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


#: When the agent verified its ticket, as its record states it.
VERIFIED_AT = "2026-11-13T12:00:00Z"

#: What the agent judged of the root cause, in the record's `impact` alone, as the shipped
#: task teaches: never as lines in the body.
IMPACT = {
    "Severity": "high",
    "Workaround": "request the last page by number",
    "Severity with the workaround": "medium",
}


def witness(repository: str) -> str:
    """The `path:line` the agent's claim rests on, at the basis commit."""
    return f"{repository} src/cursor.py:12 at {BASIS}"


def _ticket(
    batch: str, repository: str, host: str, draft: str, title: str, cause: str = NEW_ROOT_CAUSE
) -> str:
    """A ticket in onefollowup's shipped default shape: every part plain, a one-unit fix,
    prose-only evidence, and every value the priority and the board read in its record."""
    record = {
        "schema": 14,
        "root_cause": cause,
        "repository": repository,
        "created_by_run": batch,
        "owning_runs": [batch],
        "drafts": [draft],
        "basis": {repository: BASIS},
        "witnesses": [witness(repository)],
        "verified_at": VERIFIED_AT,
        "host": host,
        "frequency": "intermittent",
        "impact": IMPACT,
    }
    name = repository.rsplit("/", 1)[-1]
    sections = {
        "Root cause": (
            "A listing stops one page early, so a reader of a long listing never sees its last "
            "page of results."
        ),
        "Impact": (
            "**Who is affected.** Readers of long listings.\n"
            "**What it costs them.** The last page of results, on every long listing.\n"
            "**What it costs the product owner.** Some readers miss results each week.\n"
            "**Cost of the workaround.**\n"
            "- Applying it: one more request per listing, by the reader\n"
            "- Side effects on the outcome: none\n"
            "- Discovery: the failure names it"
        ),
        "Examples": f"{title}.",
        "Suggested fix": (
            "**Contributing locations.**\n"
            f"- {name} `src/cursor.py`: stops the cursor before the last page\n\n"
            f"### Stop the cursor after the last page — `{name}`\n\n"
            "Stop the cursor after the last page rather than before it, so every page of a "
            "listing is read. This changes behaviour without changing an interface."
        ),
        "Evidence": "A reader paged a long listing at the basis and page 9 of 9 never rendered.",
    }
    body = "".join(f"## {heading}\n\n{text}\n\n" for heading, text in sections.items())
    return (
        f'---\ntitle: "{name}: {title}"\nmetadata:\n  orchestrator.follow-up: '
        f"{json.dumps(record)}\n---\n{body.rstrip()}\n"
    )


def sources(host: str, repository: str, drafts: list[str]) -> str:
    """The `--sources-file` an evidence comment is staged with: its provenance, held as a
    ticket record's is, which the shipped comment collapses under its opening summary."""
    return json.dumps(
        {
            "host": host,
            "basis": {repository: BASIS},
            "drafts": drafts,
            "witnesses": [witness(repository)],
        }
    )


#: How `prepare`'s task opens the duplicate candidates its search found for one draft or input,
#: and how it lists each, as onefollowup's shipped task template renders them.
CANDIDATES_OPENING = re.compile(r"^Candidates on the destination for `(?P<input>[^`]+)`:$")
CANDIDATE_LINE = re.compile(r"^- `(?P<item>[^`]+)` — ")


def candidates(task: str) -> dict[str, list[str]]:
    """Each draft or input's duplicate candidates, as the rendered task lists them."""
    found: dict[str, list[str]] = {}
    listing: list[str] | None = None
    for line in task.splitlines():
        opening = CANDIDATES_OPENING.match(line)
        if opening is not None:
            listing = found.setdefault(opening["input"], [])
            continue
        if listing is None or not line.strip():
            continue
        candidate = CANDIDATE_LINE.match(line)
        if candidate is None:
            listing = None
        else:
            listing.append(candidate["item"])
    return found


def dismissed(found: dict[str, list[str]], draft: str, related: str | None) -> list[object]:
    """The answer's `dismissed`: every candidate of ``draft`` but ``related``, the one this
    batch's evidence comment is staged on, each with why its root cause differs."""
    return [
        {"item": candidate, "reason": "Its root cause is a different rule from this draft's."}
        for candidate in found.get(draft, [])
        if candidate != related
    ]


def verify(checkout: Path, batch: str, new: str, evidence: str, item: str, cause: str) -> None:
    """File the first draft as a ticket, the second as evidence on ``item``, dispose the rest."""
    store = Path(os.environ["ONEFOLLOWUP_STORE"])
    work = store / ".onefollowup"
    answers = work / "dispositions" / f"{batch}.json"
    held = json.loads(answers.read_text(encoding="utf-8"))
    shown = _run(checkout, ["onefollowup", "config", "show"])
    config = shown["config"]
    assert isinstance(config, dict), shown
    host = str(config["host_label"])
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
    provenance = work / "tasks" / batch / "evidence-sources.json"
    provenance.write_text(sources(host, repository, [evidence_draft]), encoding="utf-8")
    _run(
        checkout,
        ["onefollowup", "ticket", "comment", "--batch", batch, "--item", item]
        + ["--root-cause", cause, "--sources-file", str(provenance)],
        "The scroll-back journey failed again on a detached button while the first screen "
        "was handed off.\n",
    )
    # The shipped task's step 10: after every evidence comment, a re-estimate of that item.
    _run(checkout, ["onefollowup", "ticket", "re-estimate", "--batch", batch, item])

    # The task's rule for a ticket-filing answer: every candidate `prepare` listed under its
    # draft is related, by this batch's evidence comment staged on it, or dismissed.
    found = candidates((work / "tasks" / batch / "task.md").read_text(encoding="utf-8"))
    dispositions = [
        {
            "draft": new_draft,
            "disposition": "filed",
            "root_causes": [NEW_ROOT_CAUSE],
            "detail": "Verified at the basis.",
            "kind": "root-cause",
            "dismissed": dismissed(found, new_draft, None),
        },
        {
            "draft": evidence_draft,
            "disposition": "filed",
            "root_causes": [cause],
            "detail": "Evidence on the existing item for the same root cause.",
            "kind": "root-cause",
            "dismissed": dismissed(found, evidence_draft, item),
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


def file(checkout: Path, batch: str, cause: str) -> None:
    """File the batch's one draft as a ticket for root cause ``cause``, dismissing every
    candidate `prepare` found for it."""
    store = Path(os.environ["ONEFOLLOWUP_STORE"])
    work = store / ".onefollowup"
    answers = work / "dispositions" / f"{batch}.json"
    held = json.loads(answers.read_text(encoding="utf-8"))
    config = _run(checkout, ["onefollowup", "config", "show"])["config"]
    assert isinstance(config, dict), config
    (draft,) = held["drafts"]
    repository = "github.com/nickderobertis/some-service"
    tickets = work / "tasks" / batch / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    (tickets / f"{cause}.md").write_text(
        _ticket(batch, repository, str(config["host_label"]), draft, f"{cause} fails", cause),
        encoding="utf-8",
    )
    _run(checkout, ["onefollowup", "ticket", "validate", "--batch", batch, cause])
    _run(checkout, ["onefollowup", "ticket", "copy", "--batch", batch, cause])
    found = candidates((work / "tasks" / batch / "task.md").read_text(encoding="utf-8"))
    held["dispositions"] = [
        {
            "draft": draft,
            "disposition": "filed",
            "root_causes": [cause],
            "detail": "Verified at the basis.",
            "kind": "root-cause",
            "dismissed": dismissed(found, draft, None),
        }
    ]
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


def groom(checkout: Path, verdicts: Path) -> None:
    """Answer each ticket of every unanswered grooming shard with the verdict its title keys.

    ``verdicts`` maps a ticket's title to its verdict record less `schema_version` and
    `ticket`, its `duplicate_of` naming the other ticket by title; the shard's task names each
    ticket's id beside its title, and the agent writes the ids it read there.
    """
    store = Path(os.environ["ONEFOLLOWUP_STORE"])
    by_title = json.loads(verdicts.read_text(encoding="utf-8"))
    for task in sorted((store / ".onefollowup" / "groom").glob("*/*/task.md")):
        answer = task.with_name("verdicts.jsonl")
        if answer.is_file() and answer.read_text(encoding="utf-8").strip():
            continue
        text = task.read_text(encoding="utf-8")
        listed = dict(
            (title, ticket) for ticket, title in re.findall(r"^### (\S+): (.+)$", text, re.M)
        )
        snapshot = re.search(r"^ {4}(\S*snapshot\.json)$", text, re.M)
        assert snapshot is not None, text
        ids = {
            str(ticket["title"]): str(ticket["ticket"])
            for ticket in json.loads(Path(snapshot.group(1)).read_text(encoding="utf-8"))["tickets"]
        }
        lines = []
        for title, ticket in listed.items():
            verdict = dict(by_title[title])
            if verdict["duplicate_of"] is not None:
                verdict["duplicate_of"] = ids[verdict["duplicate_of"]]
            lines.append(json.dumps({"schema_version": 1, "ticket": ticket} | verdict))
        answer.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _run(checkout, ["onefollowup", "groom", "check", "--batch", task.parent.parent.name])


def _reply_id(answer: object) -> str:
    """The id the store gave the comment it added."""
    if isinstance(answer, dict) and isinstance(answer.get("id"), str):
        return str(answer["id"])
    raise SystemExit(f"the store named no comment id: {answer}")


def main(argv: list[str]) -> None:
    match argv:
        case ["verify", checkout, *rest]:
            verify(Path(checkout), *rest[:5])
        case ["file", checkout, batch, cause]:
            file(Path(checkout), batch, cause)
        case ["groom", checkout, verdicts]:
            groom(Path(checkout), Path(verdicts))
        case [_, checkout, batch, *_]:
            feedback(Path(checkout), batch)
        case _:
            raise SystemExit(f"scripted_agent.py: unrecognized arguments {argv}")


if __name__ == "__main__":
    main(sys.argv[1:])
