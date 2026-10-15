"""Read completed spike-brief delivery and all model attempts; never rerun a flow."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from onebudgetspec_sdk import report

from orchestrator import spike_brief

DELIVERY = "spike-brief-delivery-seconds"
TOKENS = "spike-brief-model-tokens"
HISTORY = spike_brief.DELIVERY_HISTORY


class Measurement(NamedTuple):
    value: float
    detail: str


def count(record: Mapping[str, object], field: str) -> int:
    value = record.get(field)
    if type(value) is not int or value < 0:
        raise ValueError(f"missing or invalid {field}; incomplete model telemetry")
    return value


def model(root: Path) -> Measurement:
    pointers = root / "oneharness-sessions.jsonl"
    histories: dict[str, str] = {}
    for line in pointers.read_text().splitlines():
        pointer = json.loads(line)
        if not isinstance(pointer, dict) or not isinstance(pointer.get("history_file"), str):
            raise ValueError(f"{pointers}: malformed model-history pointer")
        labels = pointer.get("labels")
        if (
            not isinstance(labels, dict)
            or not isinstance(labels.get("role"), str)
            or not labels["role"]
        ):
            raise ValueError(f"{pointers}: incomplete model side labels")
        histories[pointer["history_file"]] = str(labels)
    if not histories:
        raise ValueError("no saved model history")
    total = 0
    detail: list[str] = []
    attempts = 0
    for file, side in histories.items():
        before = attempts
        for line in Path(file).read_text().splitlines():
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{file}: malformed model history")
            if record.get("type") != "run":
                continue
            if not isinstance(record.get("harness_id"), str) or not record["harness_id"]:
                raise ValueError(f"{file}: incomplete harness identity")
            if not isinstance(record.get("status"), str) or not record["status"]:
                raise ValueError(f"{file}: incomplete model status")
            usage = record.get("usage")
            if not isinstance(usage, dict):
                raise ValueError(f"{file}: incomplete usage, including failed attempts")
            input_tokens = count(usage, "input_tokens")
            output_tokens = count(usage, "output_tokens")
            attempts += 1
            total += input_tokens + output_tokens
            detail.append(
                f"{side}; attempt {attempts}; {record.get('harness_id')}; "
                f"input {input_tokens} + output {output_tokens}; "
                f"status {record['status']}; exit {record.get('exit_code')}"
            )
        if attempts == before:
            raise ValueError(f"{file}: saved history contains no model attempts")
    return Measurement(float(total), "\n".join(detail))


def delivery(path: Path) -> Measurement:
    saved = spike_brief.read_delivery(path, complete=True)
    fields = spike_brief.DELIVERY_PHASES
    moments = [
        datetime.fromisoformat(spike_brief.timestamp(saved.get(field), field)) for field in fields
    ]
    # `read_delivery` has already refused phases out of order, so no span is negative.
    spans = [
        (right - left).total_seconds()
        for left, right in zip(moments[:-1], moments[1:], strict=True)
    ]
    return Measurement(
        sum(spans),
        "\n".join(
            f"{name}: {span} seconds"
            for name, span in zip(
                ["writer", "store publication", "channel delivery"], spans, strict=True
            )
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measure", choices=[DELIVERY, TOKENS], required=True)
    args = parser.parse_args(argv)
    try:
        parents = (
            os.environ.get("SPIKE_BRIEF_FLOWS") or os.environ.get("PLAN_CHECKLIST_FLOW") or ""
        ).split()
        if not parents:
            for path in sorted(spike_brief.runs_root().glob("*/" + spike_brief.RECEIPT)):
                receipt = spike_brief.read(path)
                if (
                    receipt is not None
                    and spike_brief.ended(receipt["writer_run"])
                    and (args.measure == TOKENS or receipt["brief"] is not None)
                ):
                    parents.append(receipt["parent_run"])
        if not parents:
            raise ValueError("no completed flow telemetry; SPIKE_BRIEF_FLOWS can select real flows")
        figures: list[float] = []
        details: list[str] = []
        for parent in parents:
            root = spike_brief.runs_root() / spike_brief.matched(
                parent, "parent", spike_brief.RUN_ID
            )
            receipt = spike_brief.saved(root / spike_brief.RECEIPT)
            if args.measure == DELIVERY:
                if receipt["brief"] is None:
                    raise ValueError(f"{parent}: delivery has not completed")
                value, detail = delivery(root / HISTORY)
            else:
                if not spike_brief.ended(receipt["writer_run"]):
                    raise ValueError(f"{parent}: model run has not ended")
                value, detail = model(spike_brief.runs_root() / receipt["writer_run"])
            figures.append(value)
            details.append(f"flow {parent}: {value}\n{detail}")
        # The per-brief budget is the largest completed flow, with all flows in detail.
        report(max(figures), "\n".join(details))
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"spike-brief-budgets: incomplete telemetry: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
