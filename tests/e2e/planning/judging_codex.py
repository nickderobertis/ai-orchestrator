#!/usr/bin/env python3
"""The paid provider of every judge the planning road runs, doubled at its binary.

`ONEHARNESS_BIN_CODEX` names this file, so every codex turn a review reaches arrives here —
the planner's `reviewer` judge and each llmlint batch of the `plan-checklist` judge alike —
and every input is recorded, one JSON line apiece, to `FAKE_PLANNING_JUDGE_LOG`.

An llmlint batch is answered by reading it the way a model would, and only that: a rule is
refused when its name heads a section of the batch's `## Rules to evaluate` carrying a
description, and the text `FAKE_PLANNING_JUDGE_REFUSES` maps that rule to appears in the batch's
target files — the fixture's own words, as the real review handed them over. Every other
rule holds. Any other turn is handed to `tests/e2e/fake_codex.py`, which answers the reviewer
(`FAKE_CODEX_ANSWERS`). Nothing above the provider is doubled: oneharness, llmlint,
onejudge and oneagentgraph all run for real.

**Before any of that it refuses a directory the way the real codex does**, and the decision
is `tests/e2e/fake_codex_untrusted_directory.py`'s own `refused`, its stderr and exit status
that module's `main`: no trust argument in argv and a working directory git places in no work
tree. A review lays its plan out in a `mktemp -d` scratch directory, which is no repository,
so a judge config missing `--skip-git-repo-check` is refused here as the real provider
refuses it, and oneharness falls through to the next identity. Each record says where the
turn ran (`cwd`), whether git placed that directory in a work tree (`repository`) — read
while it exists, since a review removes its scratch directory when it ends — and whether
the turn was refused (`refused`).

Keep this deterministic and stdlib-only — this file *is* the provider binary.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TypedDict

HERE = Path(__file__).resolve().parent
FAKE_CODEX = HERE.parent / "fake_codex.py"
sys.path.insert(0, str(HERE.parent))

import fake_codex  # noqa: E402 - this file runs as the provider binary, so its sibling is on the path only once inserted above
import fake_codex_untrusted_directory as untrusted  # noqa: E402 - the same sibling path as above

#: Where every input this provider receives is appended, one `JudgeInput` per line.
LOG_ENV = "FAKE_PLANNING_JUDGE_LOG"


class JudgeInput(TypedDict):
    """One launch this provider recorded to `LOG_ENV`.

    Declared beside the only thing that writes it, so a reader imports this rather than
    restating the shape.
    """

    #: Whether the launch was an llmlint batch rather than a reviewer turn.
    llmlint: bool
    prompt: str | None
    argv: list[str]
    #: Each target file of an llmlint batch, as the turn's directory held it.
    read: dict[str, str]
    #: The directory the turn ran in: codex's `--cd`/`-C`, else this process's.
    cwd: str
    #: Whether git placed that directory in a work tree when the launch was made.
    repository: bool
    #: Whether this provider refused the launch as an untrusted directory.
    refused: bool


#: `{rule name: text}`: refuse that rule wherever the batch's targets carry that text; a value
#: `{"absent": text}` refuses it wherever they do not.
REFUSES_ENV = "FAKE_PLANNING_JUDGE_REFUSES"

#: A file whose presence has this provider fail every turn, as an outage would: a journey
#: about a judgment that could not be made creates it, and removes it once the judge recovers.
DOWN_ENV = "FAKE_PLANNING_JUDGE_DOWN"


def _section(prompt: str, name: str) -> str:
    """The description llmlint wrote under ``### name``, up to its `Scope:` line."""
    rules = prompt.split(fake_codex.LLMLINT_RULES, 1)[1]
    head = f"{fake_codex.LLMLINT_RULE}{name}\n"
    if head not in rules:
        return ""
    return rules.split(head, 1)[1].split(fake_codex.LLMLINT_SCOPE, 1)[0].strip()


def _where(argv: list[str]) -> Path:
    """The directory the turn runs in: codex's `--cd`/`-C` where given, else this process's."""
    for flag in ("--cd", "-C"):
        if flag in argv[:-1]:
            return Path(argv[argv.index(flag) + 1])
    return Path.cwd()


def _read(batch: fake_codex.Batch, argv: list[str]) -> dict[str, str]:
    """Each target file of the batch, as the turn's working directory holds it."""
    where = _where(argv)
    read: dict[str, str] = {}
    for target in batch.targets:
        path = where / target
        read[target] = path.read_text(encoding="utf-8") if path.is_file() else ""
    return read


def _verdict(prompt: str, batch: fake_codex.Batch, read: dict[str, str]) -> str:
    refuses = json.loads(os.environ.get(REFUSES_ENV) or "{}")
    targets = "\n".join(read.values())
    answered: dict[str, fake_codex.RuleVerdict] = {}
    for name, rule in batch.rules.items():
        trigger = refuses.get(name)
        described = bool(_section(prompt, name))
        if isinstance(trigger, dict):
            refused = described and bool(read) and trigger.get("absent", "") not in targets
        else:
            refused = bool(trigger) and described and trigger in targets
        violations: list[fake_codex.Violation] = (
            [
                fake_codex.Violation(
                    file=batch.targets[0], line=1, message=f"{name}: the plan states {trigger!r}"
                )
            ]
            if refused and batch.targets
            else []
        )
        verdict = fake_codex.RuleVerdict(
            name=name,
            rationale=f"judging_codex: {name} {'fails' if violations else 'holds'}",
            holds=not violations,
            violations=violations,
        )
        if rule.conditional:
            verdict["relevant"] = True
        answered[name] = verdict
    return json.dumps(answered)


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print("codex-cli 0.0.0 (judging_codex)")
        return 0
    if (down := os.environ.get(DOWN_ENV)) and Path(down).exists():
        print("judging_codex: the provider is down for this journey", file=sys.stderr)
        return 1
    prompt = fake_codex.read_prompt(argv)
    batch = fake_codex.llmlint_batch(prompt)
    refused = untrusted.refused(argv)
    read = _read(batch, argv) if batch is not None and not refused else {}
    if log := os.environ.get(LOG_ENV):
        with open(log, "a", encoding="utf-8") as stream:
            where = untrusted.working_directory(argv)
            record = JudgeInput(
                llmlint=batch is not None,
                prompt=prompt,
                argv=argv,
                read=read,
                cwd=str(where),
                repository=untrusted.inside_a_repository(where),
                refused=refused,
            )
            stream.write(json.dumps(record) + "\n")
    if refused:
        return untrusted.main()
    if batch is None or prompt is None:
        os.execv(sys.executable, [sys.executable, str(FAKE_CODEX), *argv])
    for event in fake_codex.turn_events(_verdict(prompt, batch, read), billed=True):
        print(json.dumps(event), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
