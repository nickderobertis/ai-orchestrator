"""The two tracked files a registered repository takes, read together as content.

`config/onevcs.checkouts` says where a repository's working copies are and
`config/onevcs.rules.yml` how a change there publishes. Each refuses a half registration
at a different moment, so every way the two can disagree is demonstrated here rather
than left to a reader. What can then refuse the merge is not a third file: the adopted
`onevcs` reads each identity's required checks off the host itself, and `just repos
--audit-gate-coverage` reports them — `tests/e2e/test_merge_path_audit_e2e.py` drives
that. A tracked copy of that list used to stand here, reconciled against GitHub by a test
at the end of the gate, and every branch failed a whole gate whenever a sibling renamed a
check; it is gone rather than kept as an annotation.

Content is the whole subject: a checkout path is a claim about a directory outside
this tree. What policy the files resolve is `tests/e2e/test_repo_registry_apply_e2e.py`'s,
and reconciling them against the working copies this host really has is the uncached
`orchestrator:test-checkouts` tier's.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from registered_checkouts import RepoIdentity

from orchestrator.root import REPO_ROOT

TRACKED_CHECKOUTS = REPO_ROOT / "config" / "onevcs.checkouts"
TRACKED_RULES = REPO_ROOT / "config" / "onevcs.rules.yml"

#: The repository this registration is about, in the spellings the three files use.
OWNER = "nickderobertis"
REPOSITORY = "printobserver"
IDENTITY: RepoIdentity = f"github.com/{OWNER}/{REPOSITORY}"
#: The root this host keeps its checkouts of a dispatched-against repository under.
#: Every entry in that block of `config/onevcs.checkouts` is composed this way, and a
#: checkout's own directory name is what `onevcs` derives its alias from.
DISPATCHED_ROOT = "~/.ai-orchestrator/repos"
#: The publication checkout: what a change is merged into and fast-forwarded, and
#: what is never worked in.
PUBLICATION = f"{DISPATCHED_ROOT}/{OWNER}__{REPOSITORY}"
#: The execution checkout beside it — the safety clone each run cuts its private
#: clone from, spelled the way `~/ai-orchestrator-isolated` is spelled beside
#: `~/ai-orchestrator`.
EXECUTION = f"{PUBLICATION}-isolated"
#: A checkout entry as every line of that file is written: `~`-relative, one path, no
#: segment that could be a shell surprise or a traversal. Registration resolves an
#: identity from a checkout's own `origin` rather than from its path, so this is only
#: about the path being a path; the composition rules below are what make it the
#: right one.
WELL_FORMED = re.compile(r"~(/(?!\.\.?$)[A-Za-z0-9._-]+)+")
#: The suffix a safety clone carries, with the ordinal a host holding more than one of
#: them appends. It is a second checkout of the repository it is named after, so the
#: repository it belongs to is its name without this.
SAFETY_CLONE = re.compile(r"-isolated(-\d+)?$")
#: Text nobody meant to leave in a path. This file is read by a shell script and by
#: `onevcs`, neither of which can tell a path still to be chosen from a path.
PLACEHOLDER_TOKENS = ("TODO", "TBD", "FIXME", "XXX", "CHANGEME", "path/to", "<", ">", "...")
#: One rule of `config/onevcs.rules.yml`: its one-line `match:` flow mapping and the
#: indented fields under it. Read from the text rather than through a YAML parser
#: because the *order* of the rules is half of what is checked — first match wins, so
#: a rule below `default:` is a rule nothing reads.
RULE = re.compile(
    r"^  - match: \{host: (?P<host>[^,}]+), owner: (?P<owner>[^,}]+), name: (?P<name>[^,}]+)\}\n"
    r"(?P<fields>(?:^    [^\s#].*\n)*)",
    re.MULTILINE,
)
RULE_FIELD = re.compile(r"^    (?P<key>[a-z_]+): (?P<value>.+)$", re.MULTILINE)
#: What every rule that names `default:`'s fallthrough is measured against.
FALLTHROUGH = "\ndefault:\n"
#: The policy every single-owner repository this host dispatches against publishes
#: under: its change request merges itself once its merge path passes, with nobody
#: else in the path.
EXPECTED_POLICY = {"publication": "change-auto", "approvals": "none"}


class Registration(NamedTuple):
    """The two tracked files as content, so a defective one can be handed over too."""

    checkouts: str
    rules: str


def tracked() -> Registration:
    """The registration this repository actually carries."""
    return Registration(
        checkouts=TRACKED_CHECKOUTS.read_text(encoding="utf-8"),
        rules=TRACKED_RULES.read_text(encoding="utf-8"),
    )


def listed(checkouts: str) -> list[str]:
    """Every checkout entry, the way `scripts/apply-repo-registry.sh` reads the file."""
    return [entry for line in checkouts.splitlines() if (entry := line.partition("#")[0].strip())]


class Rule(NamedTuple):
    """One rule, with where in the file it stands."""

    identity: RepoIdentity
    fields: dict[str, str]
    #: Character offset of its `match:` line, which is what orders it against `default:`.
    at: int


def rules_of(rules: str) -> list[Rule]:
    """Every rule the tracked file states, in the order it states them."""
    return [
        Rule(
            identity=f"{found['host'].strip()}/{found['owner'].strip()}/{found['name'].strip()}",
            fields={
                field["key"]: field["value"].strip()
                for field in RULE_FIELD.finditer(found["fields"])
            },
            at=found.start(),
        )
        for found in RULE.finditer(rules)
    ]


def repository_of(entry: str) -> tuple[str | None, str]:
    """The owner and repository a checkout path names, as the two layouts spell them.

    A `<owner>__<name>` directory names both; every other entry names the repository
    alone, and its owner is whatever the checkout's own `origin` turns out to be. A
    safety clone's suffix comes off first, because it is a second checkout of the
    repository it is named after rather than a repository of its own.
    """
    name = SAFETY_CLONE.sub("", Path(entry).name)
    owner, separator, repository = name.partition("__")
    return (owner, repository) if separator else (None, name)


def complaints(registration: Registration) -> list[str]:
    """Every way the two files fail to describe one registered repository.

    One function rather than two assertions because the failures are about the files
    *together*: a checkout with no rule is a gap between them, and it is not visible
    from inside either one.
    """
    entries = listed(registration.checkouts)
    rules = rules_of(registration.rules)
    ruled = {rule.identity for rule in rules}
    ruled_names = {rule.identity.rpartition("/")[2] for rule in rules}
    found: list[str] = []

    for entry in entries:
        if WELL_FORMED.fullmatch(entry) is None:
            found.append(
                f"config/onevcs.checkouts lists {entry!r}, which is not a well-formed "
                "`~`-relative checkout path"
            )
        owner, repository = repository_of(entry)
        covered = (
            f"github.com/{owner}/{repository}" in ruled if owner else repository in ruled_names
        )
        if not covered:
            found.append(
                f"config/onevcs.checkouts lists {entry!r}, whose identity no rule in "
                "config/onevcs.rules.yml matches, so `just repos-apply` refuses it rather "
                "than letting it fall through to the reviewed default"
            )

    found += checkout_pair_complaints(registration.checkouts, entries)
    found += rule_complaints(registration.rules, rules)
    return found


def checkout_pair_complaints(checkouts: str, entries: list[str]) -> list[str]:
    """The two checkouts this repository is registered with, and nothing else.

    Two rather than one because publication and execution are separate roles: the
    publication checkout is only ever fast-forwarded, and the safety clone beside it is
    what a run cuts its private clone from. A third would be a checkout no role wants.
    """
    found: list[str] = []
    for line in checkouts.splitlines():
        if REPOSITORY not in line:
            continue
        commented = line.partition("#")[2].strip()
        if line.lstrip().startswith("#") and commented.startswith("~"):
            found.append(
                f"config/onevcs.checkouts comments out {commented!r}; a commented-out "
                "checkout is registered by nothing"
            )
        found += [
            f"config/onevcs.checkouts leaves {token!r} in {line.strip()!r}, which is a "
            "placeholder for a path still to be chosen rather than a path"
            for token in PLACEHOLDER_TOKENS
            if token in line
        ]

    named = [entry for entry in entries if repository_of(entry)[1] == REPOSITORY]
    found += [
        f"config/onevcs.checkouts spells a {REPOSITORY} checkout {entry!r}, which is not "
        f"composed under {DISPATCHED_ROOT} as `{OWNER}__{REPOSITORY}`, optionally with the "
        "`-isolated` suffix an execution clone carries"
        for entry in named
        if entry not in (PUBLICATION, EXECUTION)
    ]
    if PUBLICATION not in named:
        found.append(
            f"config/onevcs.checkouts names no publication checkout for {REPOSITORY}; "
            f"expected {PUBLICATION!r}"
        )
    if EXECUTION not in named:
        found.append(
            f"config/onevcs.checkouts names no execution checkout for {REPOSITORY}; expected "
            f"the safety clone {EXECUTION!r} beside the publication checkout"
        )
    if len(named) > 2:
        found.append(
            f"config/onevcs.checkouts lists {len(named)} checkouts of {REPOSITORY} — "
            f"{sorted(named)} — where the two roles are publication and execution"
        )
    return found


def rule_complaints(rules_text: str, rules: list[Rule]) -> list[str]:
    """The routing decision itself: its fields, and that the rule is reached at all."""
    matching = [rule for rule in rules if rule.identity == IDENTITY]
    if not matching:
        return [
            f"config/onevcs.rules.yml names no rule for {IDENTITY}, so it falls through to "
            "`default:` and every change there opens a review nobody asked for"
        ]
    fallthrough = rules_text.index(FALLTHROUGH)
    found: list[str] = []
    for rule in matching:
        if rule.fields != EXPECTED_POLICY:
            found.append(
                f"config/onevcs.rules.yml gives {IDENTITY} {rule.fields}, where the policy for "
                f"a single-owner repository this host dispatches against is {EXPECTED_POLICY}"
            )
        if rule.at > fallthrough:
            found.append(
                f"config/onevcs.rules.yml states the rule for {IDENTITY} below `default:`, "
                "where nothing reads it: first match wins and the default matches everything"
            )
    return found


#: The rule this file states for the repository above, as its own text — which is what
#: the mutations below move, extend, and remove.
RULE_BLOCK = (
    f"  - match: {{host: github.com, owner: {OWNER}, name: {REPOSITORY}}}\n"
    "    publication: change-auto\n"
    "    approvals: none\n"
)


def test_the_tracked_registration_is_complete_and_agrees_with_itself() -> None:
    """The finished tree: two files, one repository, nothing left half-said."""
    assert complaints(tracked()) == []


def test_the_publication_checkout_going_absent_is_refused() -> None:
    """A repository with no publication checkout has nowhere for a change to land."""
    registration = tracked()
    defective = registration._replace(
        checkouts=registration.checkouts.replace(f"{PUBLICATION}\n", "", 1)
    )

    assert any("names no publication checkout" in found for found in complaints(defective))


def test_the_execution_checkout_going_absent_is_refused() -> None:
    """And one with no safety clone has nowhere for a run to cut its own clone from."""
    registration = tracked()
    defective = registration._replace(
        checkouts=registration.checkouts.replace(f"{EXECUTION}\n", "", 1)
    )

    assert any("names no execution checkout" in found for found in complaints(defective))


def test_a_checkout_path_composed_some_other_way_is_refused() -> None:
    """Both entries present and consistent, and neither where the dispatcher clones.

    The pair rule cannot see this on its own: `~/printobserver` and
    `~/printobserver-isolated` are a publication checkout and an execution clone in
    every respect but the one that matters, which is that nothing puts a repository
    of this kind there.
    """
    registration = tracked()
    defective = registration._replace(
        checkouts=registration.checkouts.replace(PUBLICATION, f"~/{REPOSITORY}")
    )

    assert any("not composed under" in found for found in complaints(defective))


def test_a_checkout_path_left_as_a_placeholder_is_refused() -> None:
    """A path still to be chosen reads exactly like a path, to every reader but this."""
    registration = tracked()
    defective = registration._replace(
        checkouts=registration.checkouts.replace(
            f"{EXECUTION}\n", f"{EXECUTION}  # TODO: clone this\n", 1
        )
    )

    assert any("placeholder for a path" in found for found in complaints(defective))


def test_a_checkout_commented_out_rather_than_listed_is_refused() -> None:
    """Registered by nothing, and indistinguishable from registered in a quick read."""
    registration = tracked()
    defective = registration._replace(
        checkouts=registration.checkouts.replace(f"{EXECUTION}\n", f"# {EXECUTION}\n", 1)
    )

    assert any("registered by nothing" in found for found in complaints(defective))


def test_a_third_checkout_of_the_same_repository_is_refused() -> None:
    """Two roles, two checkouts: a third is one no role wants and nothing fast-forwards."""
    registration = tracked()
    defective = registration._replace(
        checkouts=registration.checkouts.replace(f"{EXECUTION}\n", f"{EXECUTION}\n{EXECUTION}-2\n")
    )

    assert any(f"lists 3 checkouts of {REPOSITORY}" in found for found in complaints(defective))


def test_a_checkout_no_rule_matches_is_refused() -> None:
    """The gap `just repos-apply` refuses at, found before a dispatch has paid for it."""
    registration = tracked()
    defective = registration._replace(
        checkouts=f"{registration.checkouts}{DISPATCHED_ROOT}/{OWNER}__unruled\n"
    )

    assert any(
        "no rule in config/onevcs.rules.yml matches" in found for found in complaints(defective)
    )


def test_the_rule_states_this_hosts_policy_and_nothing_else() -> None:
    """The routing decision itself, read off the file `just repos-apply` installs."""
    rule = next(rule for rule in rules_of(tracked().rules) if rule.identity == IDENTITY)

    assert rule.fields == EXPECTED_POLICY


def test_the_rule_stands_above_the_reviewed_default() -> None:
    """First match wins, so where the rule stands is the whole of whether it is read."""
    rules_text = tracked().rules
    rule = next(rule for rule in rules_of(rules_text) if rule.identity == IDENTITY)

    assert rule.at < rules_text.index(FALLTHROUGH)


def test_a_rule_stated_below_the_default_is_refused() -> None:
    """Which is why moving it there is a refusal rather than a reordering."""
    registration = tracked()
    moved = registration.rules.replace(f"\n{RULE_BLOCK}", "", 1)
    defective = registration._replace(rules=f"{moved}\n{RULE_BLOCK}")

    assert any("below `default:`" in found for found in complaints(defective))


def test_a_rule_carrying_a_field_beyond_the_policy_is_refused() -> None:
    """`publication` and `approvals` are the whole vocabulary; anything else is unread.

    A `gate:` is what this matters most about — onevcs 0.11.0 removed the concept, and
    one reintroduced under an older `version:` resolves perfectly well while quietly
    returning this host to running a verifier beside the real one.
    """
    registration = tracked()
    defective = registration._replace(
        rules=registration.rules.replace(
            RULE_BLOCK, f'{RULE_BLOCK}    gate: {{command: ["just", "gate"]}}\n', 1
        )
    )

    assert any("where the policy for a single-owner" in found for found in complaints(defective))
