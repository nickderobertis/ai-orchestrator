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

import re
import unicodedata
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Annotation-only: this module formats an env var and must stay importable
    # without dragging the ledger (and through it the merge/github/verify stack)
    # into every dispatch. `graph_labels` never constructs one of these ids, it
    # only renders ones it is handed.
    from .runs import NodeId, RunId, StepId

LABEL_ENV = "ONEHARNESS_HISTORY_LABELS"

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
MAX_VALUE_CODEPOINTS = 256


class LabelError(ValueError):
    """A label key or value violates the oneharness history-label contract."""


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

    Every component is optional: a bare ``just dispatch`` has no run or node, and
    labelling it with empty strings would violate the non-empty value rule.
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
