"""Every repository keeps the policy it published under before `onevcs` owned the registry.

This host ran on a registry of its own until the adopted stack moved that mechanism
to `onevcs` — and moved none of the data, so `just repos` listed nothing and no
lifecycle dispatch could resolve a repository at all. `just repos-apply` is the
reproducible form of carrying it across, and what it carries is a merge path: an
identity re-registered a notch wider publishes to a base branch without the review
its repository requires, and nothing downstream notices.

So the pre-adoption registry is checked in at `tests/fixtures/pre-adoption-repos.json`
and this journey holds the tracked configuration to reproducing it. It builds a
checkout per pre-adoption checkout, carrying that repository's real origin, and
drives the real recipe with the real `config/onevcs.rules.yml` against a scratch
registry root — so what is asserted is what `onevcs` itself resolves, not a reading
of the YAML.

llmlint: ignore-file[e2e_not_mocked] The scratch checkouts stand in for this host's
own clones, which a test may not register or publish from; the recipe, the script,
`onevcs`, and git are all real, and the policy asserted is the one `onevcs` resolved.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

import pytest
from registered_checkouts import TRACKED_CHECKOUTS, RepoIdentity

from orchestrator.root import REPO_ROOT

#: The registry this host ran on before `onevcs` owned one, verbatim.
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "pre-adoption-repos.json"
#: The tracked rules file the recipe installs, which decides every listed checkout's
#: publication path — and, since onevcs 0.11.0, nothing else. It names no verifier,
#: because this host runs none.
TRACKED_RULES = REPO_ROOT / "config" / "onevcs.rules.yml"

#: The schema this host's rules file declares. At 3 a `gate:` anywhere is refused by
#: `deny_unknown_fields`; at 1 and 2 it is accepted, ignored, and reported once. Both
#: halves are driven below, because "we migrated" and "the engine still tolerates an
#: unmigrated host" are separate claims and only one of them is about this file.
RULES_SCHEMA_VERSION = 3

#: The tracked inventory of what each repository's merge path really requires. It was
#: a coverage claim — which required checks this host's gate reproduced — until that
#: gate was removed; the first category is now empty by construction, so the file
#: records the merge path's own checks and every one of them can refuse a merge.
MERGE_PATH_CHECKS = REPO_ROOT / "config" / "merge-path-checks.json"

#: How every rule in the tracked file names the repository it matches. Reading them
#: back out is what makes a rule added later fail here, rather than at that
#: repository's first publication.
RULE_MATCH = re.compile(
    r"- match: \{host: (?P<host>[^,]+), owner: (?P<owner>[^,]+), name: (?P<name>[^}]+)\}"
)

RepoType = Literal["single-owner", "team"]
Workflow = Literal["local", "remote"]


class Identity(NamedTuple):
    """One repository as the pre-adoption registry described it."""

    #: The key the pre-adoption registry filed it under, which its checkouts name.
    url: str
    #: The identity `onevcs` normalizes this repository's origin URL to.
    key: RepoIdentity
    origin: str
    #: `single-owner` or `team`.
    repo_type: RepoType
    #: `local` or `remote`.
    workflow: Workflow

    @property
    def publication(self) -> str:
        """How a change here must publish now.

        This is the whole migration, stated once: the old registry split the
        decision across a repository type and a workflow, and the rules file states
        it as how a change publishes plus whether somebody else has to approve it.
        """
        if self.workflow == "local":
            return "local-direct"
        return "change-open" if self.repo_type == "team" else "change-auto"

    @property
    def approvals(self) -> str:
        """Whether somebody else has to approve a change here."""
        return "required" if self.repo_type == "team" else "none"


class Checkout(NamedTuple):
    """One local checkout as the pre-adoption registry described it."""

    path: str
    #: The pre-adoption identity URL this checkout belongs to.
    identity: str


def _golden() -> tuple[tuple[Identity, ...], tuple[Checkout, ...]]:
    """The pre-adoption registry, read once into records with a shape to check."""
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))
    identities = tuple(
        Identity(
            url=url,
            key=record["origin"].removeprefix("https://").removesuffix(".git"),
            origin=record["origin"],
            repo_type=record["repo_type"],
            workflow=record["workflow"],
        )
        for url, record in sorted(document["identities"].items())
    )
    checkouts = tuple(
        Checkout(path=record["path"], identity=record["identity"])
        for _, record in sorted(document["checkouts"].items())
    )
    return identities, checkouts


IDENTITIES, CHECKOUTS = _golden()
#: Each identity, keyed the way its checkout records name it.
BY_URL = {identity.url: identity for identity in IDENTITIES}
#: The `$HOME` the pre-adoption registry's paths are absolute under, taken from the
#: fixture itself. The registry was captured on one host and this suite runs on
#: others, so substituting *this* host's home would render the fixture's paths as
#: `~`-spellings no host has and make the tracked list unmatchable off that machine.
PRE_ADOPTION_HOME = os.path.commonpath([entry.path for entry in CHECKOUTS])


def checkout(directory: Path, origin: str) -> Path:
    """A real git checkout of `origin`, which is all registration reads of one."""
    directory.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=directory, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=directory, check=True)
    return directory


def apply_registry(manifest: Path, home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the real recipe against a scratch registry root."""
    return subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest), *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
    )


def onevcs(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--project", str(REPO_ROOT), "onevcs", *arguments],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": str(home)},
        text=True,
        capture_output=True,
    )


def ruled_identities() -> tuple[RepoIdentity, ...]:
    """Every identity `config/onevcs.rules.yml` names, in the order it names them."""
    text = TRACKED_RULES.read_text(encoding="utf-8")
    named = tuple(
        f"{rule['host']}/{rule['owner']}/{rule['name']}" for rule in RULE_MATCH.finditer(text)
    )
    assert len(named) == text.count("- match:"), (
        "config/onevcs.rules.yml no longer writes every rule's match as a one-line flow "
        f"mapping, so only {len(named)} of {text.count('- match:')} rules were read back"
    )
    return named


class MergePath(NamedTuple):
    """One identity's merge path, as `config/merge-path-checks.json` inventories it."""

    #: The branch its required checks are declared on.
    branch: str
    #: Required check → the reason slug saying what that check is.
    checks: dict[str, str]

    @property
    def required(self) -> frozenset[str]:
        """Every check the merge path requires."""
        return frozenset(self.checks)


def merge_path_checks() -> tuple[dict[RepoIdentity, MergePath], dict[str, str]]:
    """The tracked declaration, read once into records with a shape to check."""
    document = json.loads(MERGE_PATH_CHECKS.read_text(encoding="utf-8"))
    declared = {
        key: MergePath(branch=record["branch"], checks=dict(record["checks"]))
        for key, record in sorted(document["identities"].items())
    }
    return declared, dict(document["reasons"])


MERGE_PATHS, REASONS = merge_path_checks()


def reported(output: str, key: str) -> str:
    """One field of `onevcs rules check`, without its `(from rule 1)` annotation."""
    for line in output.splitlines():
        name, separator, value = line.partition(": ")
        if separator and name.strip() == key:
            return value.split(" (from ")[0].strip()
    raise AssertionError(f"no {key!r} in:\n{output}")


class Applied(NamedTuple):
    """One scratch registry, brought up to the tracked configuration."""

    home: Path
    manifest: Path
    result: subprocess.CompletedProcess[str]


class RegisteredCheckout(TypedDict):
    identity: str
    path: str


class RegistryDocument(TypedDict):
    identities: dict[str, object]
    checkouts: dict[str, RegisteredCheckout]


@pytest.fixture(scope="module")
def applied(tmp_path_factory: pytest.TempPathFactory) -> Applied:
    """The pre-adoption registry's checkouts, registered by the real recipe."""
    root = tmp_path_factory.mktemp("repo-registry")
    paths = [
        checkout(root / "checkouts" / Path(entry.path).name, BY_URL[entry.identity].origin)
        for entry in CHECKOUTS
    ]
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    result = apply_registry(manifest, home)
    assert result.returncode == 0, result.stdout + result.stderr
    return Applied(home=home, manifest=manifest, result=result)


@pytest.fixture(scope="module")
def ruled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A registry holding a checkout of *every* identity the tracked rules name.

    The `applied` fixture registers the pre-adoption checkouts, which is what the
    migration assertions are about; an identity registered since — `llmlint` is the
    first — has a rule there but no checkout, and `onevcs rules check` resolves a
    repository through a registered checkout. Registering one per rule is what lets
    the gate assertions below cover the whole file rather than only the rules that
    predate the adoption.
    """
    root = tmp_path_factory.mktemp("ruled-registry")
    paths = [
        checkout(root / "checkouts" / key.rpartition("/")[2], f"https://{key}")
        for key in ruled_identities()
    ]
    manifest = root / "checkouts.list"
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    home = root / "onevcs"
    result = apply_registry(manifest, home)
    assert result.returncode == 0, result.stdout + result.stderr
    return home


def test_every_pre_adoption_identity_and_checkout_is_registered(applied: Applied) -> None:
    registry: RegistryDocument = json.loads(
        (applied.home / "registry.json").read_text(encoding="utf-8")
    )
    assert set(registry["identities"]) == {identity.key for identity in IDENTITIES}
    actual = {(record["path"], record["identity"]) for record in registry["checkouts"].values()}
    expected = {
        (
            str(applied.manifest.parent / "checkouts" / Path(entry.path).name),
            BY_URL[entry.identity].key,
        )
        for entry in CHECKOUTS
    }
    assert actual == expected


def test_apply_reports_the_resolved_policy_table(applied: Applied) -> None:
    assert "apply-repo-registry: resolved policy" in applied.result.stdout
    assert "ai-orchestrator" in applied.result.stdout
    assert "local-direct" in applied.result.stdout
    assert "org-apps" in applied.result.stdout
    assert "change-open" in applied.result.stdout


def test_resolve_accepts_registered_spellings_and_refuses_bare_owner_name(
    applied: Applied,
) -> None:
    """Plans may name registry identities, aliases, origins, or checkout paths."""
    identity = IDENTITIES[0]
    registry: RegistryDocument = json.loads(
        (applied.home / "registry.json").read_text(encoding="utf-8")
    )
    alias, checkout_record = next(
        (alias, record)
        for alias, record in registry["checkouts"].items()
        if record["identity"] == identity.key
    )
    accepted = (
        identity.key,
        alias,
        identity.origin,
        checkout_record["path"],
    )
    for spelling in accepted:
        resolved = onevcs(applied.home, "resolve", spelling)
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        assert identity.key in resolved.stdout

    owner_name = identity.key.removeprefix("github.com/")
    refused = onevcs(applied.home, "resolve", owner_name)
    assert refused.returncode != 0, refused.stdout
    assert owner_name in refused.stderr


@pytest.mark.parametrize("identity", IDENTITIES, ids=lambda identity: identity.key)
def test_each_identity_publishes_the_way_it_did(applied: Applied, identity: Identity) -> None:
    checked = onevcs(applied.home, "rules", "check", identity.key)
    assert checked.returncode == 0, checked.stdout + checked.stderr

    assert reported(checked.stdout, "publication") == identity.publication
    assert reported(checked.stdout, "approvals") == identity.approvals
    assert reported(checked.stdout, "matched").startswith("rule ")


def test_the_team_repository_still_needs_a_review(applied: Applied) -> None:
    """The one identity where a wrong answer merges unreviewed work into a team's base."""
    checked = onevcs(applied.home, "rules", "check", "github.com/petsinc/org-apps")
    assert reported(checked.stdout, "publication") == "change-open"
    assert reported(checked.stdout, "approvals") == "required"


def test_this_repository_still_publishes_locally(applied: Applied) -> None:
    """The one identity that opens no change request at all, and merges in place."""
    checked = onevcs(applied.home, "rules", "check", "github.com/nickderobertis/ai-orchestrator")
    assert reported(checked.stdout, "publication") == "local-direct"
    assert reported(checked.stdout, "approvals") == "none"


def required_checks(key: RepoIdentity) -> tuple[frozenset[str], str | None]:
    """What GitHub really requires to merge into an identity's base branch.

    Asked of the API rather than of a workflow file, because a workflow job is not a
    required check: `llmlint` and `screencomp` both run one on every pull request and
    require neither, and a gate reproducing those would verify something that cannot
    refuse a merge. Returns the contexts, or the reason they could not be read.
    """
    repository = key.partition("/")[2]
    branch = MERGE_PATHS[key].branch
    probe = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}/branches/{branch}/protection",
            "--jq",
            "[.required_status_checks.contexts // []] | flatten | .[]",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode == 0:
        return frozenset(probe.stdout.split("\n")) - {""}, None
    # An unprotected branch is an answer, not an outage: nothing is required there.
    if "Branch not protected" in probe.stderr:
        return frozenset(), None
    diagnostic = (probe.stderr or probe.stdout).strip()
    return frozenset(), diagnostic.splitlines()[-1] if diagnostic else "gh reported no diagnostic"


#: How this gate names an identity it could not read while it could read others. A
#: partial read is the failure the gate exists to make loud, so the phrase is a
#: constant rather than a spelling: the journey below drives the real gate and asserts
#: against this, so the two cannot part company.
PARTIALLY_READ = "was not read, so this run verified nothing about that identity"
#: How it names a run that could read nothing at all, which is the one deliberate
#: skip. Same reason for being a constant.
NOTHING_READ = "no branch protection could be read at all"


def unread_report(key: RepoIdentity, diagnostic: str) -> str:
    """One identity nobody could read, with what `gh` said about it.

    The diagnostic rides along because it is what separates the three cases an
    operator has to act on differently — an expired credential, a rate limit, and a
    repository that was renamed or made private — and re-running the gate to learn
    which one it was costs another fifteen API calls and answers no faster.
    """
    return (
        f"{key}: {MERGE_PATHS[key].branch}'s branch protection {PARTIALLY_READ} "
        f"— gh said: {diagnostic}"
    )


@pytest.mark.reads_checkouts
def test_the_declared_required_checks_match_each_repositorys_branch_protection() -> None:
    """The drift gate: the declaration, against the merge paths that own the fact.

    `config/merge-path-checks.json` restates something no file in this repository
    decides — which checks another repository requires to merge — and a check added,
    renamed, or newly required after it was written would otherwise be found the way
    this host found the defect: by a dispatched branch passing its gate, publishing,
    and sitting blocked.

    This is why the tier is uncached, for the same reason the recipe reconciliation
    is: its input is other repositories' branch protection, which no `nx.json` key
    covers, and a memoized green would be a verdict on whatever they required when it
    was recorded.

    **An identity this run could not read fails it.** It used to be filed away and
    skipped over, which made a partial verification indistinguishable from a whole
    one: on 2026-08-23 a dispatch ran the complete gate over its tree at 03:31Z and
    got exit 0, and its publishing push eight minutes later ran this same uncached
    tier over that same tree and was refused, naming an onetaskgraph drift the first
    run had silently declined to ask about. The declaration this file guards says the
    same thing about itself — *"nothing is claimed by omission"* — and an identity
    nobody asked about is the largest omission available here.

    The one case that stays a skip is a run that could read *nothing*, which is a
    report about this host rather than about the inventory; it is decided explicitly
    below rather than fallen into, and says so in its own message.
    """
    if shutil.which("gh") is None:
        pytest.skip("gh is not installed, so this host cannot read any branch protection")

    answered: dict[RepoIdentity, frozenset[str]] = {}
    unread: dict[RepoIdentity, str] = {}
    for key in sorted(MERGE_PATHS):
        contexts, failure = required_checks(key)
        if failure is None:
            answered[key] = contexts
        else:
            unread[key] = failure
    if not answered:
        # The deliberate half of the rule: with nothing readable there is no partial
        # verification for a green to overstate, and a host with no network or no
        # credentials cannot be asked to verify other repositories. Every diagnostic
        # still travels, because "the token expired" and "every repository was
        # renamed" reach this line the same way and are not the same problem.
        pytest.skip(
            f"{NOTHING_READ}, so none of the {len(MERGE_PATHS)} routed identities was "
            "verified: "
            + "; ".join(f"{key}: {diagnostic}" for key, diagnostic in sorted(unread.items()))
        )

    drifted = {
        key: (contexts ^ MERGE_PATHS[key].required)
        for key, contexts in answered.items()
        if contexts != MERGE_PATHS[key].required
    }
    complaints = [
        f"{key}: config/merge-path-checks.json and {MERGE_PATHS[key].branch}'s branch "
        f"protection disagree about {sorted(difference)} — inventory each one under "
        "`checks` with the reason saying what that check is, or drop it if it no longer "
        "gates the merge"
        for key, difference in drifted.items()
    ] + [unread_report(key, diagnostic) for key, diagnostic in sorted(unread.items())]
    assert not complaints, "\n".join(
        [
            f"{len(answered)} of {len(MERGE_PATHS)} routed identities were read:",
            *complaints,
        ]
    )


GATE = test_the_declared_required_checks_match_each_repositorys_branch_protection.__name__
#: What the substituted `gh` says before the line carrying its reason, so the journey
#: below proves the report carries the *diagnostic* rather than whatever `gh` happened
#: to print first. Real `gh` prefaces an auth failure the same way.
GH_PREAMBLE = "gh: this request was not answered"


def write_fake_gh(directory: Path) -> None:
    """Install a `gh` that answers branch protection for some identities and not others.

    The remote host is the one thing here that cannot be real: driving the gate against
    GitHub would need a repository whose protection this suite could break on purpose.
    Everything above it is real — the real `gh` argv, the real probe, the real gate
    function under a real pytest — and what this program decides is only which
    identities the API answers for. It answers each readable one with exactly what
    `config/merge-path-checks.json` declares, so the readable half of a partial read
    contributes no drift of its own and the failure under test is the only one.
    """
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "gh"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        # `gh api repos/<owner>/<name>/branches/<branch>/protection --jq <expr>`
        "route = argv[1].split('/') if len(argv) > 1 and argv[0] == 'api' else []\n"
        "if len(route) < 3:\n"
        "    sys.stderr.write(f'fake gh: nothing here answers {argv}\\n')\n"
        "    raise SystemExit(1)\n"
        "key = 'github.com/' + route[1] + '/' + route[2]\n"
        "unreadable = dict(\n"
        "    pair.split('=', 1)\n"
        "    for pair in os.environ['FAKE_GH_UNREADABLE'].split(';')\n"
        "    if pair\n"
        ")\n"
        "if key in unreadable:\n"
        f"    sys.stderr.write({GH_PREAMBLE!r} + '\\n')\n"
        "    sys.stderr.write(unreadable[key] + '\\n')\n"
        "    raise SystemExit(1)\n"
        "declared = list(json.loads(\n"
        "    open(os.environ['FAKE_GH_CHECKS'], encoding='utf-8').read()\n"
        ")['identities'][key]['checks'])\n"
        "drifted = dict(\n"
        "    pair.split('=', 1)\n"
        "    for pair in os.environ['FAKE_GH_NEWLY_REQUIRED'].split(';')\n"
        "    if pair\n"
        ")\n"
        "if key in drifted:\n"
        "    declared.append(drifted[key])\n"
        "sys.stdout.write(''.join(context + '\\n' for context in declared))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


def drive_the_gate(
    tmp_path: Path,
    unreadable: dict[RepoIdentity, str],
    newly_required: dict[RepoIdentity, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real gate, under a real pytest, against that substituted remote host.

    `newly_required` is a check that repository started requiring after the inventory
    was written, which is the drift the gate has always been for: it is here so the
    two kinds of complaint can be driven together.
    """
    binaries = tmp_path / "bin"
    write_fake_gh(binaries)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"{Path(__file__).resolve()}::{GATE}",
            # `-rs` because a skip's reason is half of what is asserted here, and a
            # quiet run prints only the dot.
            "-rs",
            "--no-cov",
            "-p",
            "no:cacheprovider",
        ],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_UNREADABLE": ";".join(
                f"{key}={diagnostic}" for key, diagnostic in unreadable.items()
            ),
            "FAKE_GH_NEWLY_REQUIRED": ";".join(
                f"{key}={context}" for key, context in (newly_required or {}).items()
            ),
            "FAKE_GH_CHECKS": str(MERGE_PATH_CHECKS),
        },
        text=True,
        capture_output=True,
    )


def test_an_identity_nobody_could_read_fails_the_drift_gate(tmp_path: Path) -> None:
    """The regression: a partial read is a failure, and it names what went unasked.

    One identity refusing to answer while the rest do used to remove that identity
    from the comparison and leave the tier green, which is how a tree that had just
    passed the complete gate was refused eight minutes later by the same tier run
    from the `pre-push` hook. The diagnostic is asserted because an operator reading
    this has to tell an expired credential from a renamed repository, and re-running
    the gate to find out is fifteen more API calls and no faster.
    """
    unreadable = "github.com/nickderobertis/onetaskgraph"
    diagnostic = "gh: Not Found (HTTP 404)"

    driven = drive_the_gate(tmp_path, {unreadable: diagnostic})

    assert driven.returncode != 0, driven.stdout + driven.stderr
    assert "1 failed" in driven.stdout
    assert unreadable in driven.stdout
    assert PARTIALLY_READ in driven.stdout
    assert diagnostic in driven.stdout
    # The preamble is what `gh` said first and the diagnostic is what it said last;
    # reporting the first line would name the outage without naming its reason.
    assert GH_PREAMBLE not in driven.stdout
    assert f"{len(MERGE_PATHS) - 1} of {len(MERGE_PATHS)} routed identities were read" in (
        driven.stdout
    )
    # The other identities answered exactly what the inventory declares, so nothing
    # here is drift and none of them is named: an operator reading this failure is
    # reading one problem. Asserted per identity rather than against the drift wording,
    # which pytest also echoes back as the source of the assertion that failed.
    assert not [key for key in MERGE_PATHS if key != unreadable and key in driven.stdout]
    assert NOTHING_READ not in driven.stdout


def test_drift_and_an_unread_identity_are_reported_by_one_run(tmp_path: Path) -> None:
    """Both kinds of complaint reach the operator from the same run.

    The drift the gate has always caught and the unread identity it used to swallow
    are now one report, which is the point of aggregating them: a run that named the
    drift and then skipped over the unread identity would send an operator back for
    a second fifteen-call round to learn the other half.
    """
    unreadable = "github.com/nickderobertis/onetaskgraph"
    diagnostic = "gh: Not Found (HTTP 404)"
    drifting = "github.com/nickderobertis/crozier"
    newly_required = "coverage-floor"

    driven = drive_the_gate(tmp_path, {unreadable: diagnostic}, {drifting: newly_required})

    assert driven.returncode != 0, driven.stdout + driven.stderr
    assert f"{len(MERGE_PATHS) - 1} of {len(MERGE_PATHS)} routed identities were read" in (
        driven.stdout
    )
    assert f"{drifting}: config/merge-path-checks.json" in driven.stdout
    assert f"['{newly_required}']" in driven.stdout
    assert unread_report(unreadable, diagnostic) in driven.stdout


def test_drift_alone_still_fails_with_the_inventory_it_disagrees_with(tmp_path: Path) -> None:
    """The gate's original job, unchanged: everything read, one repository moved."""
    drifting = "github.com/nickderobertis/crozier"
    newly_required = "coverage-floor"

    driven = drive_the_gate(tmp_path, {}, {drifting: newly_required})

    assert driven.returncode != 0, driven.stdout + driven.stderr
    assert f"{len(MERGE_PATHS)} of {len(MERGE_PATHS)} routed identities were read" in driven.stdout
    assert f"{drifting}: config/merge-path-checks.json" in driven.stdout
    assert f"['{newly_required}']" in driven.stdout
    assert PARTIALLY_READ not in driven.stdout


def test_a_host_that_can_read_nothing_at_all_still_skips(tmp_path: Path) -> None:
    """The stated exception: no answers anywhere is a report about this host.

    It is a skip because there is no partial verification for a green to overstate,
    and the message says which case the reader is in — with every identity's own
    diagnostic, since a token that expired and a network that is gone arrive here the
    same way.
    """
    diagnostic = "gh: To get started with GitHub CLI, please run: gh auth login"

    driven = drive_the_gate(tmp_path, dict.fromkeys(MERGE_PATHS, diagnostic))

    assert driven.returncode == 0, driven.stdout + driven.stderr
    assert "1 skipped" in driven.stdout
    assert NOTHING_READ in driven.stdout
    assert f"none of the {len(MERGE_PATHS)} routed identities was verified" in driven.stdout
    assert diagnostic in driven.stdout


def test_every_identity_answering_the_declaration_still_passes(tmp_path: Path) -> None:
    """The green path this change must not have moved: all read, all agreeing."""
    driven = drive_the_gate(tmp_path, {})

    assert driven.returncode == 0, driven.stdout + driven.stderr
    assert "1 passed" in driven.stdout


def test_every_ruled_identity_declares_what_its_merge_path_requires() -> None:
    """A rule nobody inventoried is a merge path nobody looked at.

    The two files decide different halves of one question — `config/onevcs.rules.yml`
    how a change publishes, `config/merge-path-checks.json` what can then refuse it —
    and an identity present in only one of them is the half nobody checked.
    """
    assert set(MERGE_PATHS) == set(ruled_identities())


@pytest.mark.parametrize("key", sorted(MERGE_PATHS))
def test_every_required_check_names_a_reason_the_vocabulary_defines(key: RepoIdentity) -> None:
    """A check whose reason nothing defines renders to an operator as a blank."""
    unknown = {reason for reason in MERGE_PATHS[key].checks.values() if reason not in REASONS}
    assert not unknown, f"{key} names reasons no `reasons` entry defines: {sorted(unknown)}"


def test_every_declared_reason_is_one_some_identity_uses() -> None:
    """The vocabulary is small on purpose: an unused reason is one nobody had to justify."""
    used = {reason for declared in MERGE_PATHS.values() for reason in declared.checks.values()}
    assert set(REASONS) == used, f"unused reasons: {sorted(set(REASONS) - used)}"


def test_the_tracked_rules_declare_the_migrated_schema_and_name_no_gate() -> None:
    """The file itself: version 3, and no `gate:` anywhere in it.

    Asserted on the tracked bytes as well as on what `onevcs` resolves, because the two
    fail differently. A `gate:` reintroduced under `version: 3` is refused by the
    engine and shows up as a broken apply; one reintroduced together with a *downgrade*
    to `version: 2` resolves perfectly well, prints one deprecation nobody reads, and
    silently returns this host to running a verifier beside the real one.
    """
    text = TRACKED_RULES.read_text(encoding="utf-8")
    declared = [line for line in text.splitlines() if line.startswith("version:")]

    assert declared == [f"version: {RULES_SCHEMA_VERSION}"], declared
    named = [
        line for line in text.splitlines() if not line.lstrip().startswith("#") and "gate" in line
    ]
    assert not named, (
        "config/onevcs.rules.yml names a gate again; onevcs 0.11.0 removed the concept "
        f"and the merge path is the verifier: {named}"
    )


@pytest.mark.parametrize("key", sorted(frozenset(ruled_identities())))
def test_the_resolved_policy_is_publication_and_approvals_and_nothing_else(
    ruled: Path, key: RepoIdentity
) -> None:
    """Contract A, read off the engine: `rules check` reports no `gate:` line at all.

    The reading that matters is the tool's rather than the file's. A rules file this
    host writes could be silent about a gate while the engine still resolved one from
    a default, which is exactly the shape of the two wrong diagnoses this repository
    records — so what is asserted is the policy a publication would really run under.
    """
    checked = onevcs(ruled, "rules", "check", key)
    assert checked.returncode == 0, checked.stdout + checked.stderr

    fields = {
        line.partition(": ")[0].strip() for line in checked.stdout.splitlines() if ": " in line
    }
    assert "gate" not in fields, checked.stdout
    assert {"publication", "approvals"} <= fields, checked.stdout
    assert reported(checked.stdout, "matched").startswith("rule ")


def test_a_gate_at_the_migrated_schema_version_is_refused_by_name(tmp_path: Path) -> None:
    """The half of Contract A that keeps this file from drifting back.

    At `version: 3` a rule is `{publication, approvals}` exactly and `gate:` is an
    unknown field, so a rules file carrying one does not load. Driven through the real
    recipe because that is where an operator meets it: an apply that installed a file
    the engine cannot read would leave every publication resolving the previous rules.
    """
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    gated = tmp_path / "gated.yml"
    gated.write_text(
        f"version: {RULES_SCHEMA_VERSION}\n"
        "rules:\n"
        "  - match: {host: github.com, owner: nickderobertis, name: onevcs}\n"
        "    publication: change-auto\n"
        "    approvals: none\n"
        '    gate: {command: ["just", "gate"]}\n'
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n",
        encoding="utf-8",
    )

    result = apply_registry(manifest, tmp_path / "home", "--rules", str(gated))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "is not a valid onevcs rules file" in result.stderr
    assert "gate" in result.stderr, result.stderr
    assert not (tmp_path / "home" / "rules.yml").exists()


def test_a_gate_at_an_older_schema_version_is_accepted_ignored_and_reported(
    tmp_path: Path,
) -> None:
    """The other half: an unmigrated host keeps publishing, and is told why once.

    This is the measurement that makes the removal real rather than nominal on this
    host. The same `gate: ["false"]` that an `onevcs` before 0.11.0 would have run and
    been refused by is now read, dropped, and named on stderr — so the *engine*, not
    just this repository's file, is what stopped running a tier of its own.
    """
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    legacy = tmp_path / "legacy.yml"
    legacy.write_text(
        "version: 2\n"
        "rules:\n"
        "  - match: {host: github.com, owner: nickderobertis, name: onevcs}\n"
        "    publication: change-auto\n"
        "    approvals: none\n"
        '    gate: {command: ["false"]}\n'
        "default:\n"
        "  publication: change-open\n"
        "  approvals: required\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"

    result = apply_registry(manifest, home, "--rules", str(legacy))
    assert result.returncode == 0, result.stdout + result.stderr

    checked = onevcs(home, "rules", "check", "github.com/nickderobertis/onevcs")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "gate" not in {
        line.partition(": ")[0].strip() for line in checked.stdout.splitlines() if ": " in line
    }, checked.stdout
    assert reported(checked.stdout, "publication") == "change-auto"
    assert "version 3 removed" in checked.stderr, checked.stderr
    assert str(home / "rules.yml") in checked.stderr, checked.stderr


def test_reapplying_the_configuration_changes_nothing(applied: Applied) -> None:
    """Idempotence, which is what makes this a recipe to re-run rather than a migration."""
    document = applied.home / "registry.json"
    before = document.read_bytes()
    again = apply_registry(applied.manifest, applied.home)
    assert again.returncode == 0, again.stdout + again.stderr
    assert document.read_bytes() == before
    assert "rules      unchanged" in again.stdout


def test_dry_run_parses_comments_whitespace_and_tilde_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "operator-home"
    present = checkout(fake_home / "checkout", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text("  # comment\n  ~/checkout   # local clone\n", encoding="utf-8")
    registry_home = tmp_path / "registry"
    monkeypatch.setenv("HOME", str(fake_home))

    result = apply_registry(manifest, registry_home, "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"would register  {present}" in result.stdout
    assert "rules      would install" in result.stdout
    assert "dry run — 1 checkout(s) would be registered, 0 skipped" in result.stdout
    assert not registry_home.exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("--checkouts",), "usage: apply-repo-registry.sh"),
        (("--rules",), "usage: apply-repo-registry.sh"),
        (("--unknown",), "unknown argument"),
        (("--checkouts", "/does/not/exist"), "does not exist"),
    ],
)
def test_invalid_cli_input_is_rejected(
    tmp_path: Path, arguments: tuple[str, ...], message: str
) -> None:
    result = apply_registry(tmp_path / "unused", tmp_path / "home", *arguments)
    assert result.returncode == 2
    assert message in result.stderr


def test_help_is_successful(tmp_path: Path) -> None:
    result = apply_registry(tmp_path / "unused", tmp_path / "home", "--help")
    assert result.returncode == 0
    assert "usage: apply-repo-registry.sh" in result.stderr


# llmlint: ignore[tests_mirror_real_usage] Corrupt JSON must be seeded directly.
def test_malformed_existing_registry_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "registry.json").write_text("not json", encoding="utf-8")
    manifest = tmp_path / "checkouts"
    manifest.write_text("", encoding="utf-8")

    result = apply_registry(manifest, home)

    assert result.returncode == 1
    assert "cannot read a valid registry" in result.stderr


def test_invalid_rules_do_not_replace_the_installed_rules(tmp_path: Path) -> None:
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    installed = home / "rules.yml"
    installed.write_text("version: 2\nrules: []\n", encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("not: [valid", encoding="utf-8")

    result = apply_registry(manifest, home, "--rules", str(invalid))

    assert result.returncode == 1
    assert "is not a valid onevcs rules file" in result.stderr
    assert installed.read_text(encoding="utf-8") == "version: 2\nrules: []\n"


def test_invalid_rules_are_rejected_with_no_present_checkout(tmp_path: Path) -> None:
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{tmp_path / 'not-cloned'}\n", encoding="utf-8")
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("not: [valid", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home", "--rules", str(invalid))

    assert result.returncode == 1
    assert "is not a valid onevcs rules file" in result.stderr
    assert not (tmp_path / "home" / "rules.yml").exists()


def test_empty_onevcs_home_is_rejected(tmp_path: Path) -> None:
    result = subprocess.run(
        ["just", "repos-apply"],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": ""},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "ONEVCS_HOME is set but empty" in result.stderr


@pytest.mark.parametrize("onevcs_home", ["relative/registry", "/"])
def test_unsafe_onevcs_home_is_rejected(tmp_path: Path, onevcs_home: str) -> None:
    result = subprocess.run(
        ["just", "repos-apply"],
        cwd=REPO_ROOT,
        env={**os.environ, "ONEVCS_HOME": onevcs_home},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "must name an absolute directory other than /" in result.stderr


# llmlint: ignore[tests_mirror_real_usage] The legacy field must be seeded directly.
def test_registry_referencing_another_rules_file_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "registry.json").write_text(
        json.dumps({"rules": str(tmp_path / "elsewhere.yml")}), encoding="utf-8"
    )
    manifest = tmp_path / "checkouts"
    manifest.write_text("", encoding="utf-8")

    result = apply_registry(manifest, home)

    assert result.returncode == 1
    assert "would be ignored" in result.stderr


def test_checkout_registration_failure_is_reported(tmp_path: Path) -> None:
    not_a_checkout = tmp_path / "not-a-checkout"
    not_a_checkout.mkdir()
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{not_a_checkout}\n", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home")

    assert result.returncode == 1
    assert "registering" in result.stderr
    assert "failed" in result.stderr


def test_work_preserved_before_the_adoption_is_still_recognized(tmp_path: Path) -> None:
    """The provenance prefix is part of the registry's data, and stranding it is silent.

    `onevcs` spells its trailer keys `Onevcs-` when nothing configures one, and every
    branch this host preserved — plus every recovery attestation already on a base
    branch — carries `Orchestrator-`. A marker under an unconfigured prefix is
    neither read nor ignored: it is listed as unrecoverable and refused publication,
    so a rules file that dropped `trailer_prefix` would strand every preserved
    branch that predates the adoption while still reporting a green apply.
    """
    preserved = checkout(tmp_path / "preserved", "https://github.com/nickderobertis/onevcs.git")
    committer = ["-c", "user.email=t@example.com", "-c", "user.name=Test"]

    def commit(message: str) -> None:
        subprocess.run(["git", "add", "-A"], cwd=preserved, check=True)
        subprocess.run(["git", *committer, "commit", "-qm", message], cwd=preserved, check=True)

    (preserved / "file").write_text("base\n", encoding="utf-8")
    commit("chore: base")
    subprocess.run(["git", "checkout", "-qb", "preserved/work"], cwd=preserved, check=True)
    (preserved / "file").write_text("base\nwork\n", encoding="utf-8")
    commit("chore: orchestrated change (incomplete step)\n\nOrchestrator-Status: incomplete")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=preserved, check=True)

    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{preserved}\n", encoding="utf-8")
    home = tmp_path / "home"
    applied = apply_registry(manifest, home)
    assert applied.returncode == 0, applied.stdout + applied.stderr

    listed = onevcs(home, "recoverable")
    assert "preserved/work" in listed.stdout
    assert "incomplete step (provenance marker)" in listed.stdout
    assert "not configured to read" not in listed.stdout, listed.stdout
    checked = onevcs(home, "rules", "check", "github.com/nickderobertis/onevcs")
    assert reported(checked.stdout, "trailer_prefix") == "Orchestrator-"


def test_a_checkout_this_host_does_not_have_is_skipped(tmp_path: Path) -> None:
    """A second machine holds a subset of these checkouts, and still registers it."""
    present = checkout(tmp_path / "onevcs", "https://github.com/nickderobertis/onevcs.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{present}\n{tmp_path / 'never-cloned'}\n", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "skip       not on this host" in result.stdout
    assert "1 checkout(s) registered, 1 skipped" in result.stdout


def test_a_repository_no_rule_names_fails_the_apply(tmp_path: Path) -> None:
    """Falling through to the reviewed default is safe, and is nobody's configured policy.

    Reporting it as a pass is how a mistyped owner or a rule nobody wrote reaches a
    publication as a change request against a repository that wanted a local merge.
    """
    unlisted = checkout(tmp_path / "unlisted", "https://github.com/someone/unlisted.git")
    manifest = tmp_path / "checkouts"
    manifest.write_text(f"{unlisted}\n", encoding="utf-8")

    result = apply_registry(manifest, tmp_path / "home")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no rule in" in result.stderr
    assert "unlisted" in result.stderr


def which_checkout(path: str) -> str:
    """Which checkout a path names, whichever host's layout spelled it.

    The hosts this list serves lay the same checkouts out differently — one keeps
    this repository's clones at the top of `$HOME` and the engine repositories under
    `~/projects`, the other keeps everything under `~/projects` and lets the
    dispatcher clone the engines into `~/.ai-orchestrator/repos/<owner>__<name>` —
    so two spellings of one checkout agree on nothing but their last component, with
    that owner prefix removed. `onevcs` reads the same component to derive an alias.
    """
    return Path(path).name.rpartition("__")[2]


def ruled_repositories() -> set[str]:
    """Every repository `config/onevcs.rules.yml` names outright.

    Each rule's `match:` is a one-line flow mapping ending in the repository name, so
    this reads the tracked file rather than depending on a YAML parser this package
    does not ship. A `match:` spelled any other way is simply not found here, which
    fails the caller below rather than passing it — the safe direction, since what
    that assertion is protecting is that no checkout is registered without a rule.
    """
    return set(re.findall(r"name:\s*([\w.-]+)\s*}", TRACKED_RULES.read_text(encoding="utf-8")))


def test_the_tracked_checkout_list_holds_every_pre_adoption_checkout() -> None:
    """The recipe's default input, against the registry it was built to reproduce.

    Two claims, because the list is longer than that registry and has to be: a
    checkout a host does not have is skipped by design, so both layouts' spellings
    are tracked here and each host registers the ones it holds. So every
    pre-adoption checkout must still be listed under the spelling it was migrated
    from — dropping one silently unregisters a repository on the host that has it —
    and everything listed beside them must be a checkout `config/onevcs.rules.yml`
    states a publication path for: another spelling of one the migration covered, or
    a repository registered since under a rule of its own. A checkout of anything
    else has only the reviewed default to fall through to, which `just repos-apply`
    refuses rather than registers.

    Nothing here reads this host's own `$HOME`: the fixture's paths are absolute
    under the home of the host the registry was captured on, and rendering them
    against any other one produces spellings that could never match.
    """
    listed = {
        entry
        for line in TRACKED_CHECKOUTS.read_text(encoding="utf-8").splitlines()
        if (entry := line.partition("#")[0].strip())
    }
    migrated = {entry.path.replace(PRE_ADOPTION_HOME, "~", 1) for entry in CHECKOUTS}
    covered = {which_checkout(entry.path) for entry in CHECKOUTS} | ruled_repositories()

    assert all(entry.startswith("~/") for entry in listed), listed
    assert migrated <= listed, migrated - listed
    unruled = {entry for entry in listed if which_checkout(entry) not in covered}
    assert not unruled, unruled
