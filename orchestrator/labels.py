"""Build and validate ``ONEHARNESS_HISTORY_LABELS`` for a dispatched subprocess.

oneharness tags each recorded history entry with the labels named by this
environment variable, which lets `just history` answer "which run/round/node
produced this session?" instead of only "which session ran?".

The wire contract (oneharness 0.4.0):

* the value is a comma-separated list of ``key=value`` pairs;
* a key is 1-64 characters of ASCII letters/digits/dot/underscore/hyphen and
  starts alphanumeric;
* a value is non-empty, at most 256 Unicode code points, and contains no ``Cc``
  control characters.

This module is the single trust boundary for that contract: it validates every
label *before* it reaches a subprocess, so a malformed value fails loudly here
rather than silently corrupting a history record.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    # Annotation-only: this module formats an env var and must stay importable
    # without dragging the ledger (and through it the merge/github/verify stack)
    # into every dispatch. `graph_labels` never constructs one of these ids, it
    # only renders ones it is handed.
    from .runs import NodeId, RunId, StepId

LABEL_ENV = "ONEHARNESS_HISTORY_LABELS"
#: The label key `orchestrator.smoke` stamps its one dispatch with, so it can find
#: its own history record — and the only thing that names that dispatch's role,
#: which has no persona. It lives here because two modules read it from opposite
#: ends: `smoke` writes it and `dispatches` recognises a stamped process by it, and
#: a rename on one side alone would leave every smoke turn reporting an unknown role.
SMOKE_LABEL = "smoke"

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
MAX_VALUE_CODEPOINTS = 256
AgentRole = Literal["orchestrator", "worker", "judge", "check-in", "pr-author"]
PERSONA_AGENT_ROLES: Mapping[str, AgentRole] = {
    "orchestrator": "orchestrator",
    "check-in": "check-in",
    "pr-author": "pr-author",
}


class LabelError(ValueError):
    """A label key or value violates the oneharness history-label contract."""


def semantic_agent_labels(persona: str) -> dict[str, str]:
    """Return the authoritative semantic role labels for an agent dispatch."""
    return {
        "agent_role": PERSONA_AGENT_ROLES.get(persona, "worker"),
        "persona": persona,
    }


def validate_key(key: str) -> str:
    """Return ``key`` if it satisfies the contract, else raise LabelError."""
    if not isinstance(key, str) or _KEY.match(key) is None:
        raise LabelError(
            f"history label key {key!r} must be 1-64 ASCII letters/digits/dot/underscore/hyphen "
            "starting alphanumeric"
        )
    return key


def validate_value(value: str) -> str:
    """Return ``value`` if it satisfies the contract, else raise LabelError.

    Commas are rejected in addition to the published rules: the wire format is a
    comma-separated list with no defined escape, so a value containing one cannot
    round-trip. Rejecting it here beats emitting a list that parses back wrong.
    """
    if not isinstance(value, str) or not value:
        raise LabelError("history label value must be a non-empty string")
    if len(value) > MAX_VALUE_CODEPOINTS:
        raise LabelError(
            f"history label value exceeds {MAX_VALUE_CODEPOINTS} code points: {len(value)}"
        )
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise LabelError(f"history label value contains a control character: {value!r}")
    if "," in value:
        raise LabelError(
            f"history label value contains a comma, which the list format cannot escape: {value!r}"
        )
    return value


def format_labels(labels: Mapping[str, str]) -> str:
    """Validate and render labels as one ``ONEHARNESS_HISTORY_LABELS`` value."""
    return ",".join(f"{validate_key(k)}={validate_value(v)}" for k, v in labels.items())


def parse_labels(raw: str) -> dict[str, str]:
    """Parse an inherited value, skipping pairs that violate the contract.

    Inherited labels come from whatever invoked us, so this is lenient by design:
    an unparseable pair is dropped rather than failing a dispatch over a label the
    orchestrator did not set.
    """
    labels: dict[str, str] = {}
    for chunk in raw.split(","):
        if not chunk.strip() or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        try:
            labels[validate_key(key.strip())] = validate_value(value.strip())
        except LabelError:
            continue
    return labels


def merge_labels(inherited: str | None, own: Mapping[str, str]) -> str:
    """Layer our labels over any inherited ones, preserving the rest.

    A nested dispatch inherits the outer run's labels; keep them so a history entry
    still names the outer context, but let this run's own labels win on conflict.
    """
    merged = parse_labels(inherited) if inherited else {}
    merged.update(own)
    return format_labels(merged)


def graph_labels(
    *,
    run_id: RunId | None = None,
    round_number: int | None = None,
    node: NodeId | None = None,
    step: StepId | None = None,
) -> dict[str, str]:
    """Build the labels locating a dispatch in the tracked graph.

    Every component is optional: a lifecycle run outside any tracked graph has no
    run or node, and labelling it with empty strings would violate the non-empty
    value rule.
    """
    labels: dict[str, str] = {}
    if run_id:
        labels["run_id"] = run_id
    if round_number is not None:
        labels["round"] = str(round_number)
    if node:
        labels["node"] = node
    if step:
        labels["step"] = step
    return labels


def main(argv: list[str] | None = None) -> int:
    """Print this process's labels plus the given ones, for a tool we shell out to.

    A dispatched agent's own labels reach a subprocess through `run_onejudge`, which
    layers them in Python. A tool invoked from a `just` recipe — llmlint — has no
    such seam, and the recipe cannot layer them itself: the merge has to preserve an
    inherited value it does not know the shape of. So the merge stays here, behind a
    command the recipe can substitute, rather than being reimplemented in shell where
    the label contract could not be enforced.
    """
    parser = argparse.ArgumentParser(
        description="Render ONEHARNESS_HISTORY_LABELS: inherited labels, KEY=VALUE layered over."
    )
    parser.add_argument("labels", nargs="*", metavar="KEY=VALUE")
    args = parser.parse_args(argv)
    own: dict[str, str] = {}
    for item in args.labels:
        key, separator, value = item.partition("=")
        if not separator:
            print(f"history-labels: expected KEY=VALUE, got {item!r}", file=sys.stderr)
            return 2
        own[key] = value
    try:
        print(merge_labels(os.environ.get(LABEL_ENV), own))
    except LabelError as exc:
        print(f"history-labels: {exc}", file=sys.stderr)
        return 2
    return 0
