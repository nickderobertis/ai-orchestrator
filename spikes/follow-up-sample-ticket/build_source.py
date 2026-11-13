"""Write the sample's local Markdown item from `body.md` and the record below (throwaway spike).

The record is kept under `orchestrator.follow-up-sample`, never `orchestrator.follow-up`, so no
follow-up run reads the sample as a ticket. onetaskgraph 0.14.12 projects only strings, so every
value is stored already rendered the way the store will later render a list (one element per
line) or a map (one `KEY: VALUE` line per entry, in key order). Metadata the store itself wrote
back into the item (its copy link) is kept, so a re-run updates the same board item.
"""

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ITEM = HERE / "store" / "tasks" / "follow-up-collapsed-agent-sections" / "sample-ticket.md"
KEY = "orchestrator.follow-up-sample"

RUN = (
    "User-rulings-bind--manager-rulings-are-challengeable--and-plan-review-checks-"
    "who-owns-the-knowledge-at-a-seam"
)
OWNING_RUNS = [RUN]
DRAFTS = [f"drafts:{RUN}/drafts/20261112T195514Z-let-a-read-only-judge-decline-a-manager-ruling-t"]
TRANSCRIPTS = [f"onepipeline transcript {RUN} aio-adopt-ruling-provenance"]
BASIS = {
    "github.com/nickderobertis/onejudge": "e1299b8ac7b4131bf3b7de587dfadda70bf7ba10",
    "github.com/nickderobertis/onepipeline": "5f7f4611e1a88685dd8846b50d77d06eb50d2f89",
}


def lines(values: list[str]) -> str:
    return "\n".join(values)


def entries(values: dict[str, str]) -> str:
    return "\n".join(f"{key}: {values[key]}" for key in sorted(values))


def record(owning_runs: str | None = None) -> dict[str, str]:
    return {
        "host": "lima-hp",
        "owning_runs": lines(OWNING_RUNS) if owning_runs is None else owning_runs,
        "basis": entries(BASIS),
        "drafts": lines(DRAFTS),
        "transcripts": lines(TRANSCRIPTS),
        "witnesses": "none recorded (the source ticket, issue 2499, names no witness)",
        "verified_at": "2026-11-13T04:36:44Z",
    }


def main() -> None:
    kept: dict[str, object] = {}
    if ITEM.exists():
        text = ITEM.read_text()
        front = yaml.safe_load(text.split("---\n", 2)[1]) or {}
        kept = {k: v for k, v in (front.get("metadata") or {}).items() if k != KEY}
    owning_runs = sys.argv[1] if len(sys.argv) > 1 else None
    metadata = {KEY: record(owning_runs), **kept}
    head = [
        "---",
        'title: "[sample] follow-up ticket structure"',
        'status: "backlog"',
        'repositories: ["github.com/nickderobertis/ai-orchestrator"]',
        "metadata:",
        *(f"  {json.dumps(k)}: {json.dumps(v)}" for k, v in metadata.items()),
        "---",
        "",
    ]
    ITEM.parent.mkdir(parents=True, exist_ok=True)
    ITEM.write_text("\n".join(head) + "\n" + (HERE / "body.md").read_text())


if __name__ == "__main__":
    main()
