"""Build and validate ``ONEHARNESS_HISTORY_LABELS`` for a dispatched subprocess.

oneharness tags each recorded history entry with the labels named by this
environment variable, which lets `just history` answer "which run/round/node
produced this session?" instead of only "which session ran?".

**A dispatch inherits its `onepipeline.*` labels; this module composes the rest.**
From the release `config/onepipeline.version` adopts, the engine stamps every dispatch
it starts — a node-scope dispatch, each step of a lifecycle node, the dag-scope observer
graph and the `pr-author` drafting graph — with the six keys :data:`ENGINE_LABELS`
names, and points :data:`POINTER_FILE_ENV` at the run's own pointer file. Those are the
keys a dispatch *inherits*, and nothing here composes or overwrites one: the engine
removes every key under :data:`ENGINE_LABEL_PREFIX` from the value it inherited before
putting its own in, so a stale ``onepipeline.node`` cannot survive into a nested launch
and a key outside that prefix — this host's own ``role``, the wrappers' ``agent_role`` —
stands beside them untouched. What this module is for is that second kind: the labels
this repository adds on its own account, validated here before they reach a subprocess.

The names below are the engine's and oneharness's rather than this host's, so they are
declared once and reconciled: ``tests/test_engine_history_vocabulary.py`` reads each one
out of the registered `onepipeline` and `oneharness` checkouts at the tags those two pins
name, and fails when either stops stating it.

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
from enum import StrEnum

LABEL_ENV = "ONEHARNESS_HISTORY_LABELS"
#: The variable the engine sets to turn a dispatch's history recording on.
HISTORY_ENV = "ONEHARNESS_HISTORY"
#: The variable the engine points at the run's pointer file, absolute.
POINTER_FILE_ENV = "ONEHARNESS_HISTORY_POINTER_FILE"
#: The variable the engine **never** sets, which is what leaves a dispatch's transcripts
#: in the store the operator already reads with `oneharness history`. Named rather than
#: merely unmentioned because the failure it guards is silent: a dispatch whose store was
#: moved records exactly as much as one whose store was not, somewhere nobody looks.
NEVER_SET_ENV = "ONEHARNESS_HISTORY_DIR"
#: The pointer file's own name, under the run's directory.
POINTER_FILE_NAME = "oneharness-sessions.jsonl"

#: The prefix the engine reserves. Every key under it in an inherited value is dropped
#: before the engine composes its own, so this is the boundary between what a dispatch
#: inherits and what this host adds.
ENGINE_LABEL_PREFIX = "onepipeline."
#: The label a reader selects one run's sessions by, which is what
#: `oneharness history watch --label <key>=<run>` takes.
RUN_LABEL = f"{ENGINE_LABEL_PREFIX}run_id"
#: The qualified project id the run was launched from, absent when it names none.
PROJECT_LABEL = f"{ENGINE_LABEL_PREFIX}project"
#: Which kind of dispatch wrote the session; its values are `SCOPES`.
SCOPE_LABEL = f"{ENGINE_LABEL_PREFIX}scope"
#: The plan node, absent on the observer graph.
NODE_LABEL = f"{ENGINE_LABEL_PREFIX}node"
#: The lifecycle step, on lifecycle steps only.
STEP_LABEL = f"{ENGINE_LABEL_PREFIX}step"
#: The attempt number that dispatch's `node-dispatched` records, decimal.
ATTEMPT_LABEL = f"{ENGINE_LABEL_PREFIX}attempt"
#: Every key the engine stamps, in the order the contract states them.
ENGINE_LABELS = (
    RUN_LABEL,
    PROJECT_LABEL,
    SCOPE_LABEL,
    NODE_LABEL,
    STEP_LABEL,
    ATTEMPT_LABEL,
)


class Scope(StrEnum):
    """Every value the engine stamps under `SCOPE_LABEL`, as a closed set.

    An enum rather than three strings, because a scope is a filter somebody writes
    against a store they cannot see into: a value outside this set is one no session
    carries, and a reader filtering on it gets an empty answer rather than an error. A
    `StrEnum` member *is* its string, so it goes straight onto a label value or a
    `--label` argument and compares equal to what the engine wrote.
    """

    #: A node-scope dispatch, a lifecycle step included.
    NODE = "node"
    #: The dag-scope observer graph.
    OBSERVER = "observer"
    #: The change-request drafting graph.
    PR_AUTHOR = "pr-author"


#: Every value `SCOPE_LABEL` takes, in the order the engine's contract states them.
SCOPES = tuple(Scope)

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
