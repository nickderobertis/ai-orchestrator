"""The two JSON steps of `just smoke`'s trust probe, kept out of shell.

`prepare CONFIG SETTINGS MARKER` writes a `.claude/settings.json` whose `SessionStart`
hook touches MARKER, and prints the claude-code identities of CONFIG's `harnesses`
chain, comma-joined in the chain's order. `ran REPORT` prints the candidate a
`oneharness run --format json` report says ran, empty when none did. Each exits 1 with a
message naming what to correct when its input is not what it has to be.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import tomllib
from pathlib import Path

#: What an operator does about a report this cannot read: the report is oneharness's, so
#: the next step is to take it again and, if it repeats, to read it at its source.
RERUN = "rerun `just smoke`, and if it repeats, run the probe's `oneharness run` by hand"


#: A claude-code identity as a chain names one: the harness, or one of its variants. Held
#: to this shape because the identities are joined by commas into one `--harness` value.
CLAUDE_IDENTITY = re.compile(r"claude-code(?::[a-z0-9][a-z0-9-]*)?")
#: Any harness identity a report can name as the candidate that ran.
HARNESS_IDENTITY = re.compile(r"[a-z0-9][a-z0-9-]*(?::[a-z0-9][a-z0-9-]*)?")


def prepare(config: str, settings: str, marker: str) -> str:
    """Write the hook, and name the chain's claude-code identities."""
    try:
        chain = tomllib.loads(Path(config).read_text(encoding="utf-8")).get("harnesses")
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SystemExit(
            f"smoke-probe: cannot read {config}: {error}; restore it, then retry"
        ) from error
    if not isinstance(chain, list) or not all(isinstance(entry, str) for entry in chain):
        raise SystemExit(
            f"smoke-probe: {config}'s `harnesses` is not a list of harness ids; correct it, "
            "then retry"
        )
    claude = [entry for entry in chain if CLAUDE_IDENTITY.fullmatch(entry)]
    if not claude:
        raise SystemExit(
            f"smoke-probe: {config} names no claude-code identity in `harnesses`, so the "
            "trust probe has no dispatch identity to drive; name one there, then retry"
        )
    hook = {"type": "command", "command": f"touch {shlex.quote(marker)}"}
    # llmlint: ignore[changed_behavior_has_e2e] Reachable only when the directory the
    # smoke created for this file just before stops being writable; no journey can
    # produce that without racing the filesystem it runs on.
    try:
        Path(settings).write_text(
            json.dumps({"hooks": {"SessionStart": [{"hooks": [hook]}]}}), encoding="utf-8"
        )
    except OSError as error:
        raise SystemExit(
            f"smoke-probe: cannot write {settings}: {error}; fix its directory"
        ) from error
    return ",".join(claude)


def ran(report: str) -> str:
    """The candidate a oneharness report says ran, or the empty string for none."""
    try:
        document = json.loads(Path(report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(
            f"smoke-probe: {report} is not a readable JSON report: {error}; {RERUN}"
        ) from error
    fallback = document.get("fallback") if isinstance(document, dict) else None
    if not isinstance(fallback, dict):
        raise SystemExit(f"smoke-probe: {report} carries no `fallback` object; {RERUN}")
    candidate = fallback.get("ran")
    if candidate is not None and not (
        isinstance(candidate, str) and HARNESS_IDENTITY.fullmatch(candidate)
    ):
        raise SystemExit(f"smoke-probe: {report}'s `fallback.ran` is not a harness id; {RERUN}")
    return candidate or ""


def main(argv: list[str]) -> None:
    """Run the step named, and refuse anything else with the usage line.

    A refusal rather than a default, so a caller that misspelled a step is never read
    as a report that named no candidate.
    """
    match argv:
        case ["prepare", config, settings, marker]:
            print(prepare(config, settings, marker))
        case ["ran", report]:
            print(ran(report))
        case _:
            raise SystemExit("usage: smoke-probe.py prepare CONFIG SETTINGS MARKER | ran REPORT")


if __name__ == "__main__":
    main(sys.argv[1:])
