"""The design-review task a plan's approval is asked for by, and the adapter its completion runs.

`orchestrator/design_approval.py` is the one authority over a design document's approval, and
nothing here is a second one. This is the thin interface the engine's human items reach it
through, in three places:

* **Raising.** Once `just finish-plan` has copied a plan onto its board, :func:`raise_review`
  raises the configured `design-review` phase for that board copy: one task, holding the plan,
  that names the document and the version it is at — the approval key, as
  :func:`design_approval.current` computes it. Its dedup key carries that version, so an edited
  board copy raises a new task under the new key, and every open task for an older version of
  the same board copy is dismissed as obsolete first.
* **Completing.** An ordinary **Complete** of that task — from `just human-items complete`, or
  the browser — makes the engine run the trusted adapter `.onepipeline/config.yaml` configures,
  :func:`adapter_main`, with the version the task stored. It records exactly the record `just
  approve-design` records, through :func:`design_approval.approve`, and only when the document
  is still at that version; otherwise it records nothing and answers the version the document
  is at now, and the engine refuses the completion with "Re-read the current document",
  dismisses the task and raises one for the current version.
* **Approving by hand.** `just approve-design` records the approval, then completes the open
  task for the version it approved and dismisses any for an older one
  (:func:`settle_reviews`), so the plan's hold is released by either path.

Human items off on a host is not a failure of any of these: the approval and the launch gate
are unchanged, and there is simply no task.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from orchestrator import design_approval, human_items, plan_locations
from orchestrator.design_approval import ApprovalKey

#: The phase `.onepipeline/config.yaml` configures the design-review task under.
PHASE = "design-review"

#: Why an open task for a version the document has moved past is dismissed.
OBSOLETE = (
    "the design document changed since this task was raised; "
    "a task for the current version replaces it"
)

#: Why an open task for an older version is dismissed once a later one is approved.
SUPERSEDED = "the design document was approved at a later version"


def reviews(home: str) -> list[human_items.Item]:
    """Every open design-review task for the board copy ``home``."""
    return [
        item
        for item in human_items.open_items(plan=home, kind="task")
        if item.approval is not None and item.approval.board_copy == home
    ]


def _version(item: human_items.Item) -> str | None:
    return None if item.approval is None else item.approval.key


def raise_review(home: str, session: str) -> str:
    """Raise the design-review task for ``home`` at the version its document is at now.

    Answers one line saying what it did. Open tasks for an older version are dismissed first;
    one already open for this version is left as it is; a document already approved at this
    version raises none, and an open task for it is completed, releasing its hold.
    """
    document, key = design_approval.current(home)
    standing = reviews(home)
    for item in standing:
        if _version(item) != key:
            human_items.dismiss(item.id, OBSOLETE)
    current = [item.id for item in standing if _version(item) == key]
    if design_approval.recorded(document) == key:
        for open_id in current:
            human_items.complete(open_id)
        return f"design-review: {home}'s design document is already approved at this version"
    if current:
        return (
            f"design-review: {current[0]} already asks for {home}'s design document at this version"
        )
    raised = human_items.raise_phase(
        PHASE,
        {
            "owner": {"session": session, "run": None, "plan": home},
            "dedup_key": f"{home}:{key}",
            "vars": {"document": design_approval.located(document)},
            "approval": {"board_copy": home, "key": key},
        },
    )
    if raised is None:
        raise OSError(f"the {PHASE} phase raised nothing for {home}")
    return (
        f"design-review: raised {raised.id} asking for {home}'s design document "
        "to be read and approved"
    )


def settle_reviews(home: str, key: ApprovalKey) -> list[str]:
    """Complete ``home``'s open design-review task for ``key``, which was just approved.

    Every open task for another version is dismissed as superseded. Answers one line per task.
    """
    said = []
    for item in reviews(home):
        if _version(item) == key:
            human_items.complete(item.id)
            said.append(f"design-review: completed {item.id}, releasing {home}'s hold")
        else:
            human_items.dismiss(item.id, SUPERSEDED)
            said.append(f"design-review: dismissed {item.id}, raised for an older version")
    return said


def adapter_main(stdin: str | None = None) -> int:
    """The approval adapter: approve the version a completed task stored, or name the current one.

    Reads `{board_copy, key, actor, on_behalf_of}` and answers `{board_copy, key}`: the stored
    key when the approval of it is recorded, the current one when the document moved on and
    nothing was. Anything it cannot do exits non-zero, which leaves the task open.
    """
    text = sys.stdin.read() if stdin is None else stdin
    try:
        request = json.loads(text)
        if not isinstance(request, dict):
            raise ValueError("the request is not an object")
        board_copy, key = request.get("board_copy"), request.get("key")
        if not isinstance(board_copy, str) or not isinstance(key, str) or not key.strip():
            raise ValueError("the request names no board_copy and key")
        answered = design_approval.approve(
            design_approval.qualified(board_copy), expected=ApprovalKey(key)
        )
    except (OSError, ValueError) as exc:
        print(f"design-review adapter: {exc}; nothing was approved", file=sys.stderr)
        return 1
    if answered.moved:
        print(
            f"design-review adapter: {board_copy}'s design document changed since this task "
            f"was raised; nothing was approved",
            file=sys.stderr,
        )
    else:
        print(design_approval.said(answered), file=sys.stderr)
    print(json.dumps({"board_copy": board_copy, "key": answered.key}))
    return 0


def approve_main(argv: Sequence[str] | None = None) -> int:
    """`just approve-design`: record the approval, then settle the plan's design-review tasks."""
    parser = argparse.ArgumentParser(
        prog="approve-design",
        description="Record the user's approval of one plan project's design document.",
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    args = parser.parse_args(argv)
    try:
        project = design_approval.qualified(args.project)
        answered = design_approval.approve(project)
    except (OSError, ValueError) as exc:
        print(f"approve-design: {exc}", file=sys.stderr)
        return 1
    print(design_approval.said(answered))
    try:
        for line in settle_reviews(project, answered.key):
            print(line)
    except human_items.Unavailable:
        pass
    except OSError as exc:
        print(
            f"approve-design: the approval is recorded, but {project}'s design-review task could "
            f"not be completed ({exc}); complete it with `just human-items complete <ITEM>`",
            file=sys.stderr,
        )
    return 0


def raise_main(argv: Sequence[str] | None = None) -> int:
    """`finish-plan`'s step: raise the design-review task for the board copy a plan landed as."""
    parser = argparse.ArgumentParser(
        prog="design-review",
        description="Raise the design-review task for the board copy a plan was copied to.",
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    parser.add_argument("--in", dest="destination", required=True, metavar="SOURCE")
    args = parser.parse_args(argv)
    try:
        session = human_items.acting_session()
        if session is None:
            print(
                "design-review: this process acts as no session, so no design-review task was "
                "raised; the plan is still approved with `just approve-design`",
                file=sys.stderr,
            )
            return 0
        home = str(plan_locations.copied(args.project, args.destination).qualified_id)
        print(raise_review(home, session))
    except human_items.Unavailable:
        print("design-review: human items are off on this host, so no task was raised")
    except OSError as exc:
        print(
            f"design-review: no design-review task was raised for {args.project} ({exc}); "
            f"the plan is still approved with `just approve-design`",
            file=sys.stderr,
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """`orchestrator-design-review adapter|raise ...`."""
    match list(sys.argv[1:] if argv is None else argv):
        case ["adapter"]:
            return adapter_main()
        case ["raise", *rest]:
            return raise_main(rest)
        case _:
            print(
                "usage: orchestrator-design-review adapter | raise SOURCE:PROJECT --in SOURCE",
                file=sys.stderr,
            )
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
