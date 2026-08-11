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
