"""Record a person's approval of a plan's design document, and refuse a launch without one.

A plan is not what a person can usefully review. What they can judge is the one short
document `config/design-doc-template.md` states — what is being built and why, the
architecture, the contracts, the acceptance criteria, and the planned work as a table of
links — written for a reader who has no depth in the domain. So the thing put in front of
the user is that document, and **their approval of it is what gates dispatch**: `just
approve-design` records the approval, and a launch against a project carrying none is
refused before anything is dispatched.

Four properties are deliberate, and each is the one `orchestrator/plan_review.py` already
defends for the plan-review gate beside this one:

* **Only an approval is ever recorded.** There is no record of a rejection, so there is
  nothing to replay and nothing that could read as a decision nobody made.
* **A record is authoritative.** Reading it is the whole check; nothing re-asks, because a
  second opinion on identical content is how one plan comes to carry two verdicts.
* **There is no escape hatch** — no flag, no option, no environment variable. An escape
  here is reached under exactly the time pressure that makes skipping this a mistake.
* **The key covers the bar as well as the content.** It is a digest of the document's own
  authored content *and* of the tracked template that says what a design document is, so
  editing the document loses its approval and moving the template invalidates every
  approval granted under the previous one.

**The record goes onto the document itself**, in the open metadata map every store carries,
rather than into a file beside the plan. That is what makes it readable from whichever
store the plan is held in: it travels with the document through `just copy-plan` exactly as
a review record travels with a task, so a plan cleared where it was drafted is still cleared
once it reaches the board. `orchestrator/plan_store.py` owns the write and states what it
costs.

**One exemption exists and it is the only one.** The project a planning launch itself writes
is exempt, because that run's *output* is the plan and its design document does not exist
until the run has produced one. It is recognised by what the project says about itself —
`scripts/plan.sh` stamps :data:`PLAN_KIND` on the project it writes — rather than by its
shape, so a hand-written project that happens to have two nodes is not quietly exempt and a
planning launch that grows a third node does not quietly lose its exemption.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, NewType

from orchestrator import plan_store
from orchestrator.plan_store import StoreDocument
from orchestrator.root import REPO_ROOT

#: A digest of the bar a design document is written and read against — the tracked
#: template. Distinct from :data:`ApprovalKey`, which is a digest of one document's
#: content *under* that bar: both are hex strings of the same length and each is
#: meaningless in the other's place.
TemplateFingerprint = NewType("TemplateFingerprint", str)

#: The digest one document's authored content approved under one template hashes to —
#: what a record holds and what the launch gate compares against.
ApprovalKey = NewType("ApprovalKey", str)

#: Where one document's approval record lives: a namespaced entry of the open metadata
#: map the document already carries, so it travels with the document into whichever store
#: the plan is copied to and stays visible to anybody reading it.
RECORD_KEY = "orchestrator.design-approval"

#: What a design document is, and the only statement of it. Hashed into every key, so an
#: approval granted under one shape does not stand once that shape has moved.
TEMPLATE = Path("config") / "design-doc-template.md"

#: Where a project says what kind of plan it is. A key of this repository's own rather than
#: an `onepipeline.` one, because it is a fact about the project and not a plan field: the
#: engine never sees it, and `orchestrator/plan_store.py`'s `read_plan` drops it.
PLAN_KIND = "orchestrator.plan-kind"

#: The one value :data:`PLAN_KIND` takes, and the whole of the exemption. Every other
#: project — and one that says nothing — is gated.
PLANNING = "planning"

#: The recipe that records an approval, named in every refusal that wants one.
RECIPE = "just approve-design"

#: What a qualified id looks like before anything is asked of the store. The launch gate
#: reads a command line it deliberately does not parse — see :func:`gate_main` — so this is
#: what tells a project id from a flag value, and it is narrow on the source half because a
#: source is a configured name rather than arbitrary text.
QUALIFIED = re.compile(r"[A-Za-z0-9_.-]+:[^\s]+\Z")


class Approved(NamedTuple):
    """What one `just approve-design` did, named rather than positional."""

    #: The document the approval was recorded against.
    document: plan_store.QualifiedDocumentId
    #: Where the store says that document is, reported back rather than composed.
    location: str
    #: True when the document already carried an approval for this exact content, so
    #: nothing was written. Repeating the command is a no-op rather than a second record.
    held: bool


# llmlint: ignore[changed_behavior_has_e2e] What a moved template does is a key that no
# longer matches, and that mechanism is driven end to end by
# `tests/plan_tooling/test_approve_design_recipe_e2e.py`, which edits a real document and
# has a real launch refused for it. The only half left is *which* input moved, and driving
# that means moving a tracked file of this checkout — which every other tier of this suite
# reads concurrently — or installing a second copy of it to move the file in. What does
# catch a moved template is deterministic rather than absent: the shipped-examples test in
# `tests/test_design_approval.py` goes red until every example this repository documents as
# launchable has been read against the new shape and approved again.
def template_fingerprint(root: Path = REPO_ROOT) -> TemplateFingerprint:
    """A digest of the design-document template in force, over ``root``'s copy of it."""
    digest = hashlib.sha256()
    digest.update(TEMPLATE.as_posix().encode("utf-8"))
    digest.update(b"\0")
    digest.update((root / TEMPLATE).read_bytes())
    return TemplateFingerprint(digest.hexdigest())


def approval_key(document: StoreDocument, template: TemplateFingerprint) -> ApprovalKey:
    """The digest ``document``'s content hashes to, approved under ``template``.

    **Exactly the authored content, and the bar.** The title, the project it is a document
    of, and the prose — the three things a person read when they approved it. Nothing the
    store owns is here: the record itself lives in the metadata map, and
    `onetaskgraph.origin` is rewritten by the very copy that writes the record, so a key
    over the metadata would invalidate the approval in the act of granting it. The labels
    are out for the weaker reason that nobody approves a label.
    """
    authored = {
        "content": document.content,
        "project": document.project,
        "template": template,
        "title": document.title,
    }
    rendered = json.dumps(authored, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return ApprovalKey(hashlib.sha256(rendered.encode("utf-8")).hexdigest())


def recorded(document: StoreDocument) -> ApprovalKey | None:
    """The digest ``document``'s record names, or ``None`` when it carries no readable one.

    A record this cannot read is answered as no record, which is the safe direction: the
    document is then unapproved, which is what an unreadable approval means anyway.
    """
    held = document.metadata.get(RECORD_KEY)
    if not isinstance(held, dict):
        return None
    key = held.get("key")
    return ApprovalKey(key) if isinstance(key, str) else None


def located(document: StoreDocument) -> str:
    """Where the store says ``document`` is, in the form the store reports it.

    A link where the store puts it on a website, a path where it puts it in a file on this
    machine, and the qualified id itself when the store reports neither — never a location
    composed here, which is the same rule the document's own planned-tasks table follows.
    """
    location = document.location or {}
    for form in ("url", "path"):
        held = location.get(form)
        if isinstance(held, str) and held:
            return held
    return str(document.qualified_id)


def design_document(project: str) -> StoreDocument:
    """``project``'s design document, or ``OSError`` saying why there is not exactly one.

    A project holds documents rather than *the* document, so "the design document" is the
    one document of the project. Several is refused rather than guessed at: an approval
    recorded against the wrong one of two reads as sound from every side afterwards, and
    the person who wrote the second document is the one who can say which is which.
    """
    documents = plan_store.read_documents(project)
    if not documents:
        raise OSError(
            f"{project} holds no design document, so there is nothing a person could have "
            f"approved; a planning run's `design-doc` node writes one into the plan's own "
            f"project, and `{RECIPE} {project}` records the approval once it is there"
        )
    if len(documents) > 1:
        named = ", ".join(sorted(document.qualified_id for document in documents))
        raise OSError(
            f"{project} holds {len(documents)} documents ({named}), so which of them is the "
            f"design document cannot be decided here; leave the project one document, or "
            f"move the others to a project of their own"
        )
    return documents[0]


def exempt(project: str) -> bool:
    """Whether ``project`` says of itself that it is the plan a planning launch is writing."""
    metadata = plan_store.project_record(project).get("metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get(PLAN_KIND) == PLANNING


def approve(project: str) -> Approved:
    """Record a person's approval of ``project``'s design document, and say where it went.

    Repeating it on unchanged content writes nothing and reports the record it found, so a
    retried command and a second person running it are both harmless. Everything else —
    edited prose, a moved template — is content nobody has approved, and this is what
    approves it.
    """
    document = design_document(project)
    key = approval_key(document, template_fingerprint())
    if recorded(document) == key:
        return Approved(document.qualified_id, located(document), held=True)
    plan_store.write_document_metadata(
        document,
        RECORD_KEY,
        {"key": key, "approved_at": datetime.now(UTC).isoformat()},
    )
    return Approved(document.qualified_id, located(document), held=False)


def refusal(project: str) -> str | None:
    """Why ``project`` may not be launched, or ``None`` when it may.

    The two refusals are told apart in the text, because they owe different next actions:
    a project with no design document is waiting on the document being written, and one
    whose document is unapproved is waiting on a person reading it.
    """
    if exempt(project):
        return None
    try:
        document = design_document(project)
    except OSError as exc:
        return str(exc)
    if recorded(document) == approval_key(document, template_fingerprint()):
        return None
    return (
        f"{project}'s design document {document.qualified_id} carries no approval for what "
        f"it currently says. It is at {located(document)}: read it, and record the "
        f"approval with `{RECIPE} {project}`"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Record the user's approval of one plan's design document, from `just approve-design`."""
    parser = argparse.ArgumentParser(
        prog="approve-design",
        description="Record the user's approval of one plan project's design document.",
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    args = parser.parse_args(argv)
    try:
        answered = approve(args.project)
    except (OSError, ValueError) as exc:
        print(f"approve-design: {exc}", file=sys.stderr)
        return 1
    if answered.held:
        print(
            f"approve-design: {answered.document} already carries an approval for what it "
            f"currently says ({answered.location}); nothing was recorded"
        )
        return 0
    print(
        f"approve-design: recorded the approval of {answered.document} ({answered.location}); "
        f"editing it, or moving {TEMPLATE}, leaves it unapproved again"
    )
    return 0


def configured_sources() -> frozenset[str]:
    """Every source name this checkout's plan-store configuration names."""
    settings = plan_store.store_json(["config", "show"]).get("settings")
    if not isinstance(settings, list):
        raise OSError(f"{plan_store.STORE} returned a configuration without a settings list")
    named: set[str] = set()
    for setting in settings:
        if not isinstance(setting, dict) or not isinstance(setting.get("key"), str):
            continue
        parts = setting["key"].split(".")
        if len(parts) > 1 and parts[0] == "sources":
            named.add(parts[1])
    return frozenset(named)


def gate_main(argv: Sequence[str] | None = None) -> int:
    """Refuse a launch whose plan carries no approved design document.

    Given a launch's own arguments, **as they were typed**. This deliberately does not
    parse them: restating `onepipeline start`'s flag grammar here would be a second
    implementation of somebody else's surface, and this repository has already paid for
    one of those. What it does instead is ask the store about every argument shaped like a
    qualified id whose source this checkout configures — so a project the launch names is
    checked wherever it sits on the command line, and a flag value that is not one is not
    a project any source answers for.

    A candidate whose project cannot be read is refused rather than skipped, and that
    costs nothing a launch was going to keep: the engine reads the same project through
    the same store, so a read that fails here fails there too, and refusing early is the
    difference between a named reason and a launch that got half-way.
    """
    try:
        sources = configured_sources()
    except OSError as exc:
        print(
            f"launch-gate: this checkout's plan store could not be read ({exc}), so whether "
            f"the plan being launched has an approved design document is unknown; run `just "
            f"bootstrap` from the repository root and launch again",
            file=sys.stderr,
        )
        return 2
    refused: list[str] = []
    for argument in argv if argv is not None else sys.argv[1:]:
        if argument.startswith("-") or not QUALIFIED.fullmatch(argument):
            continue
        if argument.split(":", 1)[0] not in sources:
            continue
        try:
            reason = refusal(argument)
        except OSError as exc:
            refused.append(
                f"{argument} could not be read out of the plan store ({exc}), so whether a "
                f"person has approved its design document is unknown"
            )
            continue
        if reason is not None:
            refused.append(reason)
    for reason in refused:
        print(f"launch-gate: {reason}", file=sys.stderr)
    if refused:
        print(
            "launch-gate: nothing was dispatched. A plan is launched once the user has "
            "approved the design document it is read as, which is what a person can judge "
            "and what a plan is put in front of them as",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
