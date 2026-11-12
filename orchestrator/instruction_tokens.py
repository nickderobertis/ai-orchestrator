"""Estimated tokens of an instruction text every session here loads: two root budgets.

`agents-md-instruction-tokens` measures the root `AGENTS.md`, which `CLAUDE.md` symlinks and
every Claude Code session in a checkout of this repository loads whole before its first
turn; `planner-prompt-instruction-tokens` measures the `system_prompt` string of
`personas/planner.yaml`, which is appended to every planner dispatch's system prompt. Both
are registered in the root `budgets.yaml` and run as
``.venv/bin/python -m orchestrator.instruction_tokens <path> [--yaml-key KEY]``.

The figure is an estimate rather than a tokenizer's count: the text's UTF-8 byte count
divided by :data:`BYTES_PER_TOKEN`, rounded up. That ratio was measured once, on the root
`AGENTS.md`, as the difference in input tokens between two Claude Code turns with and
without the file loaded as `CLAUDE.md` (spike report
`agents-md-length-spike-cut-analysis-report`), and the planner prompt is prose of the same
kind. A byte count is what the file's author controls, measures in a fraction of a second
and reproduces anywhere, which a paid tokenizer call is not.

``<path>`` is read relative to the repository root this module sits in, whatever directory
onebudgetspec runs the command from. With ``--yaml-key`` the text measured is the string the
document's top-level ``KEY`` holds, as YAML loads it, so comments and quoting are left out.
The figure is reported through onebudgetspec's SDK to the file ``ONEBUDGETSPEC_RESULT``
names, with a detail giving the byte count, the word count and the ratio; a run that is not
a budget's command, a missing file, an unreadable document or a missing key exits 1 naming
why, and reports nothing.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections.abc import Sequence
from fractions import Fraction
from typing import TextIO

import yaml
from onebudgetspec_sdk import report

from orchestrator.root import REPO_ROOT

#: Bytes of this repository's instruction prose per Claude token, measured on the root
#: `AGENTS.md` (188,809 B against 57,421 input tokens, Claude Code 2.1.322 on
#: `claude-opus-5-5`); the one statement of the ratio both budgets divide by.
BYTES_PER_TOKEN = 3.288
#: The file onebudgetspec names for a ``reported`` budget's result.
RESULT_VARIABLE = "ONEBUDGETSPEC_RESULT"


class Unmeasurable(Exception):
    """A text this command cannot measure, with the reason."""


def instruction_text(relative: str, yaml_key: str | None = None) -> str:
    """The text ``relative`` holds, or the top-level string its ``yaml_key`` holds."""
    path = REPO_ROOT / relative
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise Unmeasurable(f"{relative} does not exist under {REPO_ROOT}") from error
    except (OSError, UnicodeDecodeError) as error:
        raise Unmeasurable(f"{relative} could not be read as UTF-8 text: {error}") from error
    if yaml_key is None:
        return text
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise Unmeasurable(f"{relative} is not a YAML document: {error}") from error
    value = document.get(yaml_key) if isinstance(document, dict) else None
    if not isinstance(value, str):
        raise Unmeasurable(f"{relative} has no top-level string under the key {yaml_key!r}")
    return value


def tokens(text: str) -> int:
    """The estimated tokens of ``text``: its UTF-8 bytes over the ratio, rounded up.

    Divided exactly, as the decimal ratio is written, so a text of a whole number of
    tokens is not rounded up past it by a binary float's error.
    """
    return math.ceil(Fraction(len(text.encode("utf-8"))) / Fraction(str(BYTES_PER_TOKEN)))


def detail(text: str) -> str:
    """What the figure was computed from: bytes, words and the ratio."""
    return f"{len(text.encode('utf-8'))} B, {len(text.split())} words, {BYTES_PER_TOKEN} B/tok"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m orchestrator.instruction_tokens",
        description=__doc__.splitlines()[0] if __doc__ else None,
    )
    parser.add_argument("path", help="the file to measure, relative to the repository root")
    parser.add_argument(
        "--yaml-key", help="measure the top-level string this key holds instead of the file"
    )
    return parser


def main(argv: Sequence[str] | None = None, err: TextIO | None = None) -> int:
    """Report the named text's estimated tokens as the running budget's result."""
    err = sys.stderr if err is None else err
    arguments = _parser().parse_args(argv)
    try:
        if not os.environ.get(RESULT_VARIABLE):
            raise Unmeasurable(
                f"{RESULT_VARIABLE} names no result file; run this as the budget's command, "
                "through onebudgetspec check"
            )
        text = instruction_text(arguments.path, arguments.yaml_key)
        report(tokens(text), detail(text))
    except Unmeasurable as refusal:
        print(f"instruction-tokens: {refusal}", file=err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
