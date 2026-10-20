"""Launch the detached local writer and complete its advisory whole-plan copy/delivery."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import cast

from onetaskgraph_sdk import CopyReport, QueryResponseOfQualifiedProject

from orchestrator import design_approval, plan_store, spike_brief, spike_flow
from orchestrator.root import REPO_ROOT

NODE = "spike-brief"


def recorded_project(
    payload: CopyReport | QueryResponseOfQualifiedProject,
    source: plan_store.QualifiedProjectId,
    expected: object,
) -> plan_store.QualifiedProjectId:
    """One recorded plans identity must be what the SDK returned, on either copy or read."""
    arrived: list[object] = []
    if isinstance(payload, CopyReport):
        for item in payload.items:
            outcome = item.root
            # A failed or skipped item reached no destination, so it records no board project.
            if outcome.action == "failed" or outcome.action == "skipped":
                continue
            if outcome.source.model_dump() != source:
                continue
            if outcome.destination is not None:
                arrived.append(outcome.destination.model_dump())
    else:
        arrived = [item.id.model_dump() for item in payload.items]
    candidate = arrived[0] if len(arrived) == 1 else None
    if (
        not isinstance(candidate, str)
        or not spike_brief.QUALIFIED.fullmatch(candidate)
        or not candidate.startswith("plans:")
        or candidate != expected
    ):
        raise ValueError(
            f"expected one plans project recorded as {expected!r}; SDK returned {arrived!r}"
        )
    return plan_store.QualifiedProjectId(candidate)


def copied_project(
    report_text: str, project: plan_store.QualifiedProjectId
) -> plan_store.QualifiedProjectId:
    rows = [json.loads(line) for line in report_text.splitlines() if line.startswith("{")]
    report = CopyReport.model_validate({"items": rows})
    recorded = plan_store.project_record(project).get("metadata")
    links = recorded.get(plan_store.COPIES_KEY) if isinstance(recorded, dict) else None
    expected = links.get("plans") if isinstance(links, dict) else None
    return recorded_project(report, project, expected)


def held_project(
    shown: QueryResponseOfQualifiedProject,
    source: plan_store.QualifiedProjectId,
    named: plan_store.QualifiedProjectId,
) -> plan_store.StoreProject:
    """The board project the store shows as ``named``, held to the copy of ``source`` it is."""
    recorded_project(shown, source, named)
    held = shown.items[0].item
    if held.metadata.get(plan_store.ORIGIN_KEY) != source:
        raise ValueError("recorded board project belongs to another source project")
    return plan_store.StoreProject(
        named,
        held.title,
        held.metadata,
        held.location.model_dump(mode="python") if held.location else None,
    )


def board_project(
    source: plan_store.QualifiedProjectId, named: plan_store.QualifiedProjectId
) -> plan_store.StoreProject:
    shown = plan_store.complete(plan_store.sdk(plan_store.client().project_show(named)))
    return held_project(shown, source, named)


def command(argv: list[str], *, input: str | None = None) -> str:
    result = subprocess.run(
        argv, input=input, text=True, capture_output=True, timeout=600, check=False
    )
    if result.returncode:
        raise OSError(f"{argv[0]} refused ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def engine(*argv: str) -> str:
    return command([str(REPO_ROOT / ".venv/bin/onepipeline"), *argv])


def acknowledge(receipt: spike_brief.Receipt) -> None:
    retries = 1
    while True:
        try:
            engine(
                "unwatched",
                "--session",
                receipt["manager_session"],
                "--acknowledge",
                receipt["writer_run"],
                "--reason",
                "Advisory local preview; planning never waits for this writer or its approval",
            )
            return
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"spike brief advisory: writer acknowledgement failed: {exc}", file=sys.stderr)
            if not retries:
                raise
            retries -= 1


def launch(project: str, parent: str, spikes: str) -> None:
    spike_brief.matched(project, "plan_project", spike_brief.QUALIFIED)
    if not project.startswith("authoring:"):
        raise ValueError("plan_project must belong to authoring")
    spike_brief.matched(spikes, "spikes_run", spike_brief.RUN_ID)
    agents = spike_flow.spike_nodes(plan_store.QualifiedProjectId(project + "-spikes"))
    if not agents:
        return
    path = spike_brief.runs_root() / spike_brief.matched(parent, "parent_run", spike_brief.RUN_ID)
    path /= spike_brief.RECEIPT
    with spike_brief.locked(path):
        receipt = spike_brief.read(path)
        if receipt is None:
            receipt = spike_brief.validate(
                {
                    "schema_version": 1,
                    "plan_project": project,
                    "board_project": None,
                    "parent_run": parent,
                    "spikes_run": spikes,
                    "writer_run": parent + "-spike-brief",
                    "manager_session": spike_brief.text(
                        os.environ.get("ONEPIPELINE_LAUNCHER_SESSION"), "manager_session"
                    ),
                    "brief": None,
                    "approval": None,
                }
            )
            history_path = path.parent / spike_brief.DELIVERY_HISTORY
            if history_path.exists():
                spike_brief.read_delivery(history_path)
            else:
                spike_brief.write_delivery(
                    history_path, {"schema_version": 1, "eligible": spike_brief.now()}
                )
            spike_brief.write(path, receipt)
        if receipt["plan_project"] != project or receipt["spikes_run"] != spikes:
            raise ValueError("existing receipt belongs to another flow")
        writer = receipt["writer_run"]
        if (spike_brief.runs_root() / writer / "launch.json").exists():
            acknowledge(receipt)
            return
        loader = engine(
            "template",
            "resolve",
            "plan-task",
            "--template-root",
            str(REPO_ROOT / "templates"),
            "--json",
        )
        loader_path = path.parent / "spike-brief-task-loader.json"
        loader_path.write_text(loader)
        client = plan_store.client()
        writer_project = "authoring:" + writer
        if writer_project not in {
            str(item.qualified_id) for item in plan_store.read_projects("authoring")
        }:
            plan_store.sdk(
                client.project_create(
                    "authoring",
                    writer,
                    "Spike brief writer",
                    metadata=[
                        "onepipeline.schema_version=3",
                        'onepipeline.goal={"text":"Write the local spike brief"}',
                        "onepipeline.name=" + json.dumps(writer),
                        design_approval.PLAN_KIND
                        + "="
                        + json.dumps({"kind": "planning", "nodes": [NODE]}),
                    ],
                )
            )
        if not plan_store.read_tasks(writer_project):
            plan_store.sdk(
                client.task_create(
                    "authoring",
                    writer,
                    f"Spike brief for {project}",
                    template_loader=str(loader_path),
                    metadata=[
                        "onepipeline.id=" + json.dumps(NODE),
                        "onepipeline.persona=" + json.dumps("../personas/spike-brief.yaml"),
                        "onepipeline.agent_graph=" + json.dumps("graphs/spike-brief.yaml"),
                        "onepipeline.max_turns=3",
                    ],
                    answers={
                        "what": f"Read local authoring project {project} and its spike tasks in "
                        f"{project}-spikes. Render exactly one spike-brief document of {project}, "
                        f"with native document ID {parent}-spike-brief. Actual agents: "
                        + ", ".join(agents),
                        "why": (
                            "A short preview lets people stop unnecessary measurements "
                            "while planning proceeds."
                        ),
                        "acceptance_criteria": [
                            "The local document renders spike-brief with complete answers, "
                            "exact agent coverage, and its independent word caps."
                        ],
                        "additional_info": (
                            "Direct dispatch in the shared launch checkout. Write only outside "
                            "tracked files; do not commit, cut branches or leave its base. This "
                            "document is exempt from the shared committing requirement. Read "
                            "only local authoring records. Never call the board. The flow "
                            "publishes after you finish. Checks owed: inspect the local stored "
                            "rendering against the template and agent IDs."
                        ),
                        "budgets": [],
                    },
                )
            )
        env = os.environ.copy()
        env["ONEPIPELINE_RUN_ID"] = writer
        result = subprocess.run(
            [
                str(REPO_ROOT / "scripts/onepipeline.sh"),
                "start",
                writer_project,
                "--detach",
                "--dag-graph",
                "off",
                "--success-hook",
                str(REPO_ROOT / "scripts/spike-brief-ready.sh"),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise OSError(f"brief writer launch refused: {result.stderr.strip()}")
        print(result.stdout, end="")
        acknowledge(receipt)


def template_digest(loader: object) -> str:
    """The digest the engine's `template resolve spike-brief --json` answer states."""
    if not isinstance(loader, dict) or not isinstance(loader.get("digest"), str):
        raise ValueError("template resolver returned no digest")
    spike_brief.matched(loader["digest"], "template digest", design_approval.DIGEST)
    if loader.get("reference") != spike_brief.TEMPLATE_REFERENCE:
        raise ValueError("template resolver returned another template")
    return cast(str, loader["digest"])


def document(receipt: spike_brief.Receipt) -> plan_store.StoreDocument:
    docs = [
        doc
        for doc in plan_store.read_documents(receipt["plan_project"])
        if isinstance(doc.metadata.get(design_approval.PROVENANCE), dict)
        and cast(dict[str, object], doc.metadata[design_approval.PROVENANCE]).get("template")
        == spike_brief.TEMPLATE_REFERENCE
    ]
    if len(docs) != 1:
        raise ValueError(f"expected exactly one spike-brief rendering, found {len(docs)}")
    doc = docs[0]
    provenance = cast(dict[str, object], doc.metadata[design_approval.PROVENANCE])
    if provenance.get("body_digest") != "sha256:" + spike_brief.digest(doc.content):
        raise ValueError("stored brief body was edited after rendering")
    resolved = template_digest(
        json.loads(
            engine(
                "template",
                "resolve",
                "spike-brief",
                "--template-root",
                str(REPO_ROOT / "templates"),
                "--json",
            )
        )
    )
    if provenance.get("digest") != resolved:
        raise ValueError("stored brief uses an obsolete template")
    held = plan_store.sdk(plan_store.client().document_answers(str(doc.qualified_id)))
    agents = spike_flow.spike_nodes(
        plan_store.QualifiedProjectId(receipt["plan_project"] + "-spikes")
    )
    spike_brief.answers(held.model_dump(), agents, doc.content)
    return doc


def ready(path: Path, *, blocking: bool = True) -> None:
    if not path.exists():
        return
    with spike_brief.locked(path, blocking=blocking) as acquired:
        if not acquired:
            print(
                "spike brief advisory: delivery is already running; planning proceeds",
                file=sys.stderr,
            )
            return
        receipt = spike_brief.saved(path)
        acknowledge(receipt)
        # The writer is detached: a resume must never read a partially written document.
        spike_flow.settled(spike_flow.RunId(receipt["writer_run"]))
        history_path = path.parent / spike_brief.DELIVERY_HISTORY
        history = spike_brief.read_delivery(history_path)
        if "writer_finished" not in history:
            history["writer_finished"] = spike_brief.now()
            spike_brief.write_delivery(history_path, history)
        doc = document(receipt)
        content_digest = spike_brief.digest(doc.content)
        if receipt["brief"]:
            if receipt["brief"]["digest"] != content_digest:
                raise ValueError("surfaced brief content changed; its approval is invalidated")
            # Rewritten whole each time: it completes a history the surfacing outran, and
            # leaves a complete one as it was.
            spike_brief.write_delivery(
                history_path,
                spike_brief.completed_delivery(history, receipt["brief"]["surfaced_at"]),
            )
            return
        named = receipt["board_project"]
        # A brief finishing after its spikes ended is still copied and linked; the check-in
        # below is then retrospective and offers no approval.
        if named is None:
            copied = command(
                [
                    "just",
                    "--justfile",
                    str(REPO_ROOT / "justfile"),
                    "--working-directory",
                    str(REPO_ROOT),
                    "copy-plan",
                    receipt["plan_project"],
                ]
            )
            named = copied_project(copied, receipt["plan_project"])
            receipt["board_project"] = named
            spike_brief.write(path, receipt)
        board = board_project(receipt["plan_project"], named)
        url = spike_brief.https(plan_store.located(board.location, str(board.qualified_id)))
        if "copy_finished" not in history:
            history["copy_finished"] = spike_brief.now()
            spike_brief.write_delivery(history_path, history)
        retrospective = spike_brief.ended(receipt["spikes_run"])
        note = f"Spike brief for {receipt['plan_project']}; spikes {receipt['spikes_run']}: {url}. "
        note += (
            "The spikes have ended; this link is retrospective."
            if retrospective
            else f"Optional user approval: just approve-spikes {receipt['plan_project']}. "
            "Relay immediately and repeat while visible. Only cancel/drop/retry/amend steer work."
        )
        command(
            [
                str(REPO_ROOT / ".venv/bin/onepipeline"),
                "surface",
                "--kind",
                "check-in",
                receipt["parent_run"],
            ],
            input=note,
        )
        delivered = spike_brief.now()
        receipt["brief"] = {
            "document": doc.qualified_id,
            "url": url,
            "digest": content_digest,
            "surfaced_at": delivered,
        }
        # llmlint: ignore-block[changed_behavior_has_e2e] The window between a delivered
        # surface and its receipt save cannot be reached deterministically without the
        # idempotent surface delivery the pinned engine lacks. The drafted follow-up
        # "Add idempotent planner surface delivery" owns this gap; delete this block when it lands.
        spike_brief.write(path, receipt)
        # llmlint: ignore-end[changed_behavior_has_e2e]
        history["delivered"] = delivered
        spike_brief.write_delivery(history_path, history)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)
    started = verbs.add_parser("launch")
    for field in ("project", "parent", "spikes"):
        started.add_argument(field)
    resumed = verbs.add_parser("resume")
    resumed.add_argument("parent")
    verbs.add_parser("hook")
    args = parser.parse_args()
    retry = "inspect the malformed receipt before retrying its parent flow"
    try:
        match args.verb:
            case "launch":
                retry = shlex.join(
                    [
                        "uv",
                        "run",
                        "python",
                        "-m",
                        "orchestrator.spike_brief_flow",
                        "launch",
                        args.project,
                        args.parent,
                        args.spikes,
                    ]
                )
                launch(args.project, args.parent, args.spikes)
            case "resume":
                retry = shlex.join(
                    [
                        "uv",
                        "run",
                        "python",
                        "-m",
                        "orchestrator.spike_brief_flow",
                        "resume",
                        args.parent,
                    ]
                )
                parent = spike_brief.matched(args.parent, "parent_run", spike_brief.RUN_ID)
                ready(spike_brief.runs_root() / parent / spike_brief.RECEIPT, blocking=False)
            case "hook":  # pragma: no branch - argparse admits only these three verbs
                writer = spike_brief.matched(
                    os.environ.get("ONEPIPELINE_RUN_ID"), "writer_run", spike_brief.RUN_ID
                )
                for path in spike_brief.runs_root().glob("*/" + spike_brief.RECEIPT):
                    receipt = spike_brief.read(path)
                    if receipt and receipt["writer_run"] == writer:
                        retry = shlex.join(
                            [
                                "uv",
                                "run",
                                "python",
                                "-m",
                                "orchestrator.spike_brief_flow",
                                "resume",
                                receipt["parent_run"],
                            ]
                        )
                        ready(path)
    except (OSError, ValueError, subprocess.TimeoutExpired, spike_flow.Stop) as exc:
        print(f"spike brief advisory: {exc}; after repairing it, {retry}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
