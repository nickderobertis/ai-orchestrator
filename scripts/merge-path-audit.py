#!/usr/bin/env python3
"""Say what `onevcs repos --audit-gates` cannot: which required checks no gate here runs.

The published audit answers whether *something* verifies an identity's merge path —
an executable `pre-push` hook, or the host's required checks — and prints one
`merge-path coverage:` line per checkout saying which. Read as "covered", that line
is wrong for every remote-publishing identity on this host, in two ways at once.

The hook it names is often not a gate at all: four of the identities registered here
report coverage by a `pre-push` hook that is a screencomp visual-regression guard,
which re-captures screenshots and never runs the identity's gate. And even a hook
that does run the gate is not the merge path — a pull request merges when GitHub's
required checks pass, and a gate running less than they do verifies a branch that
cannot merge. `nick-derobertis-site` reported exactly that coverage, published PR
#77, and had it refused by the required `llmlint` check.

So this filter rewrites each of those lines from what verifies the identity into what
can still refuse it, out of the required checks recorded in
`config/merge-path-checks.json`. Everything else in the audit is forwarded verbatim.
An identity that file does not classify is reported as unknown rather than covered,
because the direction this has to fail in is the one that sends somebody to look.

Keep this deterministic and stdlib-only: `scripts/repos.sh` spawns it as a
subprocess, resolving this repository's interpreter when one exists and a bare
`python3` otherwise, so it cannot import `orchestrator`.
"""

from __future__ import annotations

import json
import re
import sys
from typing import TypedDict

#: One repository as `onevcs` names it: `host/owner/name`. The audit heads each block
#: with one, the declaration files identities under them, and every lookup below is
#: keyed by one — the same vocabulary `tests/e2e/test_repo_registry_apply_e2e.py`
#: names, so the two files talk about an identity in one spelling rather than two.
RepoIdentity = str
#: One required check as the merge path names it, which is what an operator reads back.
Check = str
#: A key into `reasons`, never the prose itself: the declaration says *why* nothing here
#: runs a check by naming one of a small closed vocabulary, and `identity` below refuses
#: a record naming one that `reasons` does not define rather than rendering a blank.
Reason = str

#: The audit line that heads one identity's block, which is what the coverage lines
#: below it are about.
IDENTITY = re.compile(r"^(?P<identity>[^\s/]+/[^\s/]+/[^\s/]+)\t")
#: The published line this filter rewrites, keeping the indentation it arrived with.
COVERAGE = re.compile(r"^(?P<indent>\s*)merge-path coverage: (?P<verifier>.*)$")
#: The declaration's own keys, in the same `host/owner/name` shape the audit prints —
#: a key of any other shape can never match an identity, so it is a silent omission.
IDENTITY_KEY = re.compile(r"[^\s/]+/[^\s/]+/[^\s/]+")
#: What the published audit says instead of going silent when the registry holds
#: nothing. It is a complete audit carrying no record rather than a stream this filter
#: failed to read, and recognizing it is what keeps the check below from refusing the
#: one host state where there is genuinely nothing to rewrite.
NOTHING_REGISTERED = "no repositories registered"
#: The one `version` of `config/merge-path-checks.json` this filter reads. A newer
#: schema is refused rather than read as this one, which is the difference between a
#: report that is wrong and a report that says it cannot be made.
VERSION = 1


class Identity(TypedDict):
    """One identity's merge path, as `config/merge-path-checks.json` records it."""

    branch: str
    #: Required check → the commands this host's identity gate runs for it.
    gate_runs: dict[Check, list[str]]
    #: Required check → the slug in `reasons` saying why nothing here runs it.
    not_run: dict[Check, Reason]


class Declaration(TypedDict):
    """The tracked declaration, as much of it as this filter reads."""

    #: The closed vocabulary every `not_run` slug is resolved through, into its prose.
    reasons: dict[Reason, str]
    identities: dict[RepoIdentity, Identity]


class Malformed(Exception):
    """The declaration cannot be read as one, with the correction that would fix it."""


def strings(value: object, where: str) -> dict[str, str]:
    """`value` as an object of non-empty strings, or a diagnostic naming where it belongs.

    Empty is refused with the rest: a reason with no prose renders as a required check
    followed by nothing, which reads as though the check were explained.
    """
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(entry, str) and entry for key, entry in value.items()
    ):
        raise Malformed(f"{where} must be an object mapping names to non-empty strings")
    return value


def commands(runs: object, where: str) -> dict[Check, list[str]]:
    """Each required check's gate commands, refused unless every one of them is nameable.

    A check declared with no command, or with an empty one, claims coverage that names
    nothing — and `test_every_declared_gate_command_is_in_the_rule_gate` would find
    every gate satisfies it, which is the reassurance this whole surface exists to stop.
    """
    if not isinstance(runs, dict) or not all(
        isinstance(check, str)
        and check
        and isinstance(entries, list)
        and entries
        and all(isinstance(command, str) and command for command in entries)
        for check, entries in runs.items()
    ):
        raise Malformed(
            f"{where} must map each required check to a non-empty list of the commands "
            "this host's gate runs for it"
        )
    return runs


def identity(record: object, key: RepoIdentity, reasons: dict[Reason, str]) -> Identity:
    """One identity's record, checked before anything indexes into it.

    This is the trust boundary the filter has: the declaration decides what an operator
    is told can refuse a merge, so a record missing a field, or naming a reason nothing
    defines, has to be refused by name rather than reached and raised over.
    """
    if IDENTITY_KEY.fullmatch(key) is None:
        raise Malformed(f"identities.{key} is not a `host/owner/name` repository identity")
    if not isinstance(record, dict):
        raise Malformed(f"identities.{key} must be an object")
    branch = record.get("branch")
    if not isinstance(branch, str) or not branch:
        raise Malformed(f"identities.{key}.branch must be the base branch its checks run on")
    not_run = strings(record.get("not_run"), f"identities.{key}.not_run")
    undefined = sorted(set(not_run.values()) - set(reasons))
    if undefined:
        raise Malformed(
            f"identities.{key}.not_run names reasons {undefined} that the top-level "
            "`reasons` object does not define"
        )
    return {
        "branch": branch,
        "gate_runs": commands(record.get("gate_runs"), f"identities.{key}.gate_runs"),
        "not_run": not_run,
    }


def load(path: str) -> Declaration:
    """The tracked declaration, validated before any of it is believed."""
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise Malformed("the declaration must be a JSON object")
    if document.get("version") != VERSION:
        raise Malformed(
            f"`version` must be {VERSION}, the schema this filter reads, not "
            f"{document.get('version')!r}"
        )
    reasons = strings(document.get("reasons"), "reasons")
    identities = document.get("identities")
    if not isinstance(identities, dict):
        raise Malformed("`identities` must be an object keyed by `host/owner/name`")
    return {
        "reasons": reasons,
        "identities": {key: identity(record, key, reasons) for key, record in identities.items()},
    }


def verdict(declaration: Declaration, identity: RepoIdentity | None) -> tuple[str, list[str]]:
    """What to say about `identity`: the clause to end its coverage line with, and detail.

    The detail lines are returned separately because they are only worth printing when
    there is something to name — a merge path this host's gate matches whole should
    read as one line, not as a heading over nothing.
    """
    record = declaration["identities"].get(identity or "")
    if record is None:
        named = identity or "this checkout's identity"
        return (
            f" — coverage unknown: config/merge-path-checks.json records no required "
            f"checks for {named}, so nothing here says one will not refuse the merge",
            [],
        )
    missing = sorted(record["not_run"].items())
    if not missing:
        return " — and this host's gate runs every required check on it", []
    checks, each = ("check", "which can") if len(missing) == 1 else ("checks", "each able to")
    detail = [
        f"{len(missing)} required {checks} on {record['branch']} that this host's gate "
        f"does not run, {each} refuse the merge:"
    ]
    detail += [f"  {name} — {declaration['reasons'][reason]}" for name, reason in missing]
    return " — not the whole merge path", detail


def recognized(lines: list[str]) -> bool:
    """Whether this is the audit the filter knows how to rewrite, or something else.

    The other half of the trust boundary. `onevcs repos --audit-gates` is a published
    command whose output this rewrites by matching two line shapes, and neither is a
    contract it owes: a released spelling of `merge-path coverage:`, or of the tab that
    ends an identity heading, would leave every line unmatched. Forwarded verbatim that
    exits 0, so the operator gets the published claim — the one that reads as coverage
    and hid PR #77 — from the command whose whole purpose is to contradict it.

    An audit is recognized on either shape rather than on both, because an identity with
    no registered checkout heads a block with no coverage line under it, and a coverage
    line arrives with no heading above it when the audit is read from partway in. The
    empty registry is recognized on neither, which is why its own answer is named here:
    demanding a record would refuse the one host state that has none to give.
    """
    return any(
        IDENTITY.match(line) or COVERAGE.match(line) or line.strip() == NOTHING_REGISTERED
        for line in lines
    )


def augment(lines: list[str], declaration: Declaration) -> list[str]:
    """The audit, with every coverage claim replaced by what can still overturn it."""
    augmented: list[str] = []
    identity: RepoIdentity | None = None
    for line in lines:
        heading = IDENTITY.match(line)
        if heading is not None:
            identity = heading.group("identity")
        covered = COVERAGE.match(line)
        if covered is None:
            augmented.append(line)
            continue
        clause, detail = verdict(declaration, identity)
        indent = covered.group("indent")
        augmented.append(f"{indent}merge-path coverage: {covered.group('verifier')}{clause}")
        augmented += [f"{indent}  {entry}" for entry in detail]
    return augmented


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: merge-path-audit.py <merge-path-checks.json>\n"
            "  reads `onevcs repos --audit-gates` output on stdin; "
            "invoke through `just repos --audit-gate-coverage`",
            file=sys.stderr,
        )
        return 2
    try:
        declaration = load(argv[1])
    # `UnicodeDecodeError` is named alongside the rest because a declaration that is not
    # UTF-8 at all fails in the read, before any of the validation below can see it.
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, Malformed) as failure:
        print(
            f"merge-path-audit.py: {argv[1]} is not a readable merge-path declaration: "
            f"{failure}; correct that file, then rerun `just repos --audit-gate-coverage`",
            file=sys.stderr,
        )
        return 2
    text = sys.stdin.read()
    if not text:
        return 0
    lines = text.split("\n")
    # A trailing newline splits into a final empty field; keeping it is what makes the
    # rejoin below reproduce the input's own termination rather than adding or eating one.
    trailing = lines.pop() if lines and lines[-1] == "" else None
    if not recognized(lines):
        print(
            "merge-path-audit.py: the audit on stdin carries no identity heading and no "
            "`merge-path coverage:` line, so `onevcs repos --audit-gates` is no longer "
            "producing the report this filter rewrites. Forwarding it would print the "
            "published coverage claim under a command that exists to contradict it. "
            "Reconcile this filter with the published output, then rerun "
            "`just repos --audit-gate-coverage`",
            file=sys.stderr,
        )
        return 2
    output = augment(lines, declaration)
    if trailing is not None:
        output.append(trailing)
    # llmlint: ignore[tool_output_is_signal] This is a filter, not a command: its stdout
    # is the audit an operator asked for, one report per registered checkout, and it is
    # exactly as long as the registry is. Summarizing it would delete the answer.
    sys.stdout.write("\n".join(output))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main(sys.argv))
