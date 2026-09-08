"""The three tracked files a registered repository takes, read together as content.

`config/onevcs.checkouts` says where a repository's working copies are,
`config/onevcs.rules.yml` how a change there publishes, and
`config/merge-path-checks.json` what can then refuse that merge. Each refuses a half
registration at a different moment — one of them only once work has been dispatched —
so every way the three can disagree is demonstrated here rather than left to a reader.

Content is the whole subject: a checkout path is a claim about a directory outside
this tree. What policy the files resolve is `tests/e2e/test_repo_registry_apply_e2e.py`'s,
and reconciling them against the working copies and branch protection this host really
has is the uncached `orchestrator:test-checkouts` tier's.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from registered_checkouts import RepoIdentity

from orchestrator.root import REPO_ROOT

TRACKED_CHECKOUTS = REPO_ROOT / "config" / "onevcs.checkouts"
TRACKED_RULES = REPO_ROOT / "config" / "onevcs.rules.yml"
MERGE_PATH_CHECKS = REPO_ROOT / "config" / "merge-path-checks.json"
MERGE_PATH_AUDIT = REPO_ROOT / "scripts" / "merge-path-audit.py"

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
#: The base branch a change here merges into, and so the branch whose required checks
#: `config/merge-path-checks.json` is about.
BASE_BRANCH = "main"

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
    """The three tracked files as content, so a defective one can be handed over too."""

    checkouts: str
    rules: str
    inventory: str


def tracked() -> Registration:
    """The registration this repository actually carries."""
    return Registration(
        checkouts=TRACKED_CHECKOUTS.read_text(encoding="utf-8"),
        rules=TRACKED_RULES.read_text(encoding="utf-8"),
        inventory=MERGE_PATH_CHECKS.read_text(encoding="utf-8"),
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
    """Every way the three files fail to describe one registered repository.

    One function rather than three assertions because the failures are about the files
    *together*: a checkout with no rule and a rule with no inventory entry are each a
    gap between two of them, and neither is visible from inside either one.
    """
    entries = listed(registration.checkouts)
    rules = rules_of(registration.rules)
    ruled = {rule.identity for rule in rules}
    ruled_names = {rule.identity.rpartition("/")[2] for rule in rules}
    inventoried: set[RepoIdentity] = set(json.loads(registration.inventory)["identities"])
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

    found += [
        f"config/onevcs.rules.yml routes {identity}, which config/merge-path-checks.json "
        "does not inventory, so the merge-path audit reports it as coverage unknown"
        for identity in sorted(ruled - inventoried)
    ]
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


def audit(stream: str) -> subprocess.CompletedProcess[str]:
    """The real merge-path audit filter, over a real audit and the tracked declaration.

    No network and no registry: the filter is handed the identity headings the
    published audit prints and the file that decides the verdict it appends, which is
    the whole of what this is about. What `onevcs` itself would say about a checkout
    on this host is `tests/e2e/test_merge_path_audit_e2e.py`'s subject.
    """
    return subprocess.run(
        [sys.executable, str(MERGE_PATH_AUDIT), str(MERGE_PATH_CHECKS)],
        input=stream,
        text=True,
        capture_output=True,
    )


def audit_stream(*identities: RepoIdentity) -> str:
    """One block per identity, shaped the way `onevcs repos --audit-gates` prints them."""
    return "".join(
        f"{identity}\tremote\tsingle-owner\t<no-op>\n"
        f"  /checkouts/{identity.rpartition('/')[2]}\n"
        "    merge-path coverage: the host's required checks\n"
        for identity in identities
    )


def test_the_tracked_registration_is_complete_and_agrees_with_itself() -> None:
    """The finished tree: three files, one repository, nothing left half-said."""
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


def test_a_rule_with_no_inventory_entry_is_refused() -> None:
    """The other half of the same gap: routed, and the audit says nothing about it."""
    registration = tracked()
    uninventoried = (
        f"  - match: {{host: github.com, owner: {OWNER}, name: uninventoried}}\n"
        "    publication: change-auto\n"
        "    approvals: none\n\n"
    )
    defective = registration._replace(
        rules=registration.rules.replace(RULE_BLOCK, uninventoried + RULE_BLOCK, 1)
    )

    assert any("does not inventory" in found for found in complaints(defective))


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


def test_the_audit_classifies_this_identity_from_the_committed_inventory() -> None:
    """The entry earns the answer it was written for: inventoried, with nothing to list.

    An empty required-check set is the honest entry here — nothing has created a check
    on that path yet — and the audit has to render it as a merge path it knows about
    rather than as one nobody recorded.
    """
    result = audit(audit_stream(IDENTITY))

    assert result.returncode == 0, result.stderr
    assert (
        "merge-path coverage: the host's required checks — and "
        f"config/merge-path-checks.json inventories no required check on {BASE_BRANCH}, "
        "so nothing recorded here can refuse a merge"
    ) in result.stdout
    assert "coverage unknown" not in result.stdout


def test_the_audit_still_reports_an_identity_it_has_no_entry_for_as_unknown() -> None:
    """The direction that has to keep failing loudly, from the same run and the same file.

    Omitting the identity rather than inventorying it empty would land here — and this
    verdict reads as a gap to go and look at, which is what an empty entry must never
    be confused with.
    """
    unknown: RepoIdentity = f"github.com/{OWNER}/never-registered"

    result = audit(audit_stream(IDENTITY, unknown))

    assert result.returncode == 0, result.stderr
    assert (
        f"coverage unknown: config/merge-path-checks.json records no required checks for {unknown}"
    ) in result.stdout
